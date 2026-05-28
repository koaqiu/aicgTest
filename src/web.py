#!/usr/bin/env python3
"""Single-process web server: static frontend + queued image detection API."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import jwt
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from redis import Redis

from ai_detector import AIDetector
from ai_detector.dire_detector import cleanup as cleanup_dire_runtime


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_base_path(value: str) -> str:
    raw = (value or "").strip()
    if not raw or raw == "/":
        return ""
    if not raw.startswith("/"):
        raw = f"/{raw}"
    return raw.rstrip("/")


def _route(path: str) -> str:
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{WEB_BASE_PATH}{path}" if WEB_BASE_PATH else path


ROOT_DIR = Path(__file__).resolve().parents[1]
MAX_UPLOAD_BYTES = int(os.getenv("WEB_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
CACHE_TTL_SECONDS = int(os.getenv("WEB_CACHE_TTL_SECONDS", str(1800)))
TASK_RETENTION_SECONDS = int(os.getenv("WEB_TASK_RETENTION_SECONDS", str(3600)))
SHUTDOWN_GRACE_SECONDS = int(os.getenv("WEB_SHUTDOWN_GRACE_SECONDS", str(20)))
UPLOAD_DIR = Path(
    os.getenv("WEB_UPLOAD_DIR", str(Path(tempfile.gettempdir()) / "ai-detector-web-uploads"))
)
REDIS_URL = os.getenv("WEB_REDIS_URL", "redis://127.0.0.1:6379/0")
MAX_QUEUE_SIZE = int(os.getenv("WEB_MAX_QUEUE_SIZE", "128"))
IP_LIMIT_PER_MINUTE = int(os.getenv("WEB_IP_LIMIT_PER_MINUTE", "20"))
IP_LIMIT_PER_DAY = int(os.getenv("WEB_IP_LIMIT_PER_DAY", "300"))
ALLOWED_ORIGINS = {
    x.strip() for x in os.getenv("WEB_ALLOWED_ORIGINS", "").split(",") if x.strip()
}
TURNSTILE_ENABLED = _env_bool("WEB_TURNSTILE_ENABLED", False)
TURNSTILE_SITE_KEY = os.getenv("WEB_TURNSTILE_SITE_KEY", "").strip()
TURNSTILE_SECRET_KEY = os.getenv("WEB_TURNSTILE_SECRET_KEY", "").strip()
TURNSTILE_VERIFY_URL = os.getenv(
    "WEB_TURNSTILE_VERIFY_URL", "https://challenges.cloudflare.com/turnstile/v0/siteverify"
).strip()
TURNSTILE_MODE = os.getenv("WEB_TURNSTILE_MODE", "adaptive").strip().lower()
TURNSTILE_RISK_MINUTE_THRESHOLD = int(
    os.getenv("WEB_TURNSTILE_RISK_MINUTE_THRESHOLD", str(max(3, IP_LIMIT_PER_MINUTE // 2)))
)
TURNSTILE_RISK_DAY_THRESHOLD = int(
    os.getenv("WEB_TURNSTILE_RISK_DAY_THRESHOLD", str(max(10, IP_LIMIT_PER_DAY // 2)))
)
TURNSTILE_RISK_QUEUE_THRESHOLD = int(
    os.getenv("WEB_TURNSTILE_RISK_QUEUE_THRESHOLD", str(max(1, MAX_QUEUE_SIZE // 2)))
)
ABUSE_STRIKE_THRESHOLD = int(os.getenv("WEB_ABUSE_STRIKE_THRESHOLD", "3"))
ABUSE_STRIKE_TTL_SECONDS = int(os.getenv("WEB_ABUSE_STRIKE_TTL_SECONDS", "900"))
ABUSE_FORCE_CAPTCHA_SECONDS = int(os.getenv("WEB_ABUSE_FORCE_CAPTCHA_SECONDS", "1800"))
JWT_ENABLED = _env_bool("WEB_JWT_ENABLED", False)
JWT_SECRET = os.getenv("WEB_JWT_SECRET", "").strip()
JWT_ALGORITHMS = [
    x.strip() for x in os.getenv("WEB_JWT_ALGORITHMS", "HS256").split(",") if x.strip()
]
JWT_BYPASS_RATE_LIMIT = _env_bool("WEB_JWT_BYPASS_RATE_LIMIT", True)
JWT_BYPASS_CAPTCHA = _env_bool("WEB_JWT_BYPASS_CAPTCHA", True)
JWT_REQUIRE_BYPASS_CLAIM = _env_bool("WEB_JWT_REQUIRE_BYPASS_CLAIM", True)
JWT_BYPASS_CLAIM_KEY = os.getenv("WEB_JWT_BYPASS_CLAIM_KEY", "role").strip()
JWT_BYPASS_CLAIM_VALUE = os.getenv("WEB_JWT_BYPASS_CLAIM_VALUE", "internal").strip()
WEB_BASE_PATH = _normalize_base_path(os.getenv("WEB_BASE_PATH", ""))


@dataclass
class TaskState:
    task_id: str
    status: str
    created_at: float
    updated_at: float
    device: str
    file_path: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    cached: bool = False


app = FastAPI(title="AI Detector Web API", version="1.0.0")
app.mount(_route("/static"), StaticFiles(directory=str(ROOT_DIR / "web_static"), html=False), name="static")

_tasks: dict[str, TaskState] = {}
_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
_redis: Redis | None = None
_worker_task: asyncio.Task | None = None
_detector_pool: dict[str, AIDetector] = {}
_shutting_down = False
_mem_rate_minute: dict[str, tuple[int, int]] = {}
_mem_rate_day: dict[str, tuple[str, int]] = {}
_mem_abuse_state: dict[str, tuple[int, float]] = {}


def _now() -> float:
    return time.time()


def _cache_key(task_id: str) -> str:
    return f"aidet:result:{task_id}"


def _rate_minute_key(ip: str, minute_epoch: int) -> str:
    return f"aidet:rl:minute:{ip}:{minute_epoch}"


def _rate_day_key(ip: str, day_str: str) -> str:
    return f"aidet:rl:day:{ip}:{day_str}"


def _abuse_strike_key(ip: str) -> str:
    return f"aidet:abuse:strike:{ip}"


def _abuse_forced_key(ip: str) -> str:
    return f"aidet:abuse:forced:{ip}"


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "").strip()
    if xff:
        return xff.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _origin_allowed(request: Request) -> bool:
    if not ALLOWED_ORIGINS:
        return True
    origin = request.headers.get("origin")
    if not origin:
        return True
    return origin in ALLOWED_ORIGINS


def _extract_bearer_token(request: Request) -> str:
    auth = request.headers.get("authorization", "").strip()
    if not auth:
        return ""
    parts = auth.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return ""
    return parts[1].strip()


def _decode_jwt_token(token: str) -> dict[str, Any]:
    if not JWT_ENABLED:
        return {}
    if not JWT_SECRET:
        raise HTTPException(status_code=500, detail="服务端 JWT 未配置 secret")
    if not JWT_ALGORITHMS:
        raise HTTPException(status_code=500, detail="服务端 JWT 未配置算法")

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=JWT_ALGORITHMS)
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(status_code=401, detail="JWT 已过期") from e
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail="JWT 无效") from e

    if not isinstance(payload, dict):
        raise HTTPException(status_code=401, detail="JWT 负载无效")
    return payload


def _jwt_context(request: Request) -> tuple[bool, dict[str, Any] | None]:
    if not JWT_ENABLED:
        return False, None

    token = _extract_bearer_token(request)
    if not token:
        return False, None

    payload = _decode_jwt_token(token)
    return True, payload


def _jwt_bypass_allowed(payload: dict[str, Any] | None) -> bool:
    if payload is None:
        return False

    if not JWT_REQUIRE_BYPASS_CLAIM:
        return True

    if not JWT_BYPASS_CLAIM_KEY:
        return False

    expected = JWT_BYPASS_CLAIM_VALUE
    actual = payload.get(JWT_BYPASS_CLAIM_KEY)
    if isinstance(actual, list):
        return expected in [str(x) for x in actual]
    return str(actual) == expected


def _check_rate_limit(ip: str) -> tuple[bool, str | None]:
    now = int(time.time())
    minute_epoch = now // 60
    day_str = datetime.utcnow().strftime("%Y%m%d")

    if _redis is not None:
        try:
            minute_key = _rate_minute_key(ip, minute_epoch)
            day_key = _rate_day_key(ip, day_str)

            minute_count = int(_redis.incr(minute_key))
            if minute_count == 1:
                _redis.expire(minute_key, 120)

            day_count = int(_redis.incr(day_key))
            if day_count == 1:
                _redis.expire(day_key, 60 * 60 * 24 * 2)

            if minute_count > IP_LIMIT_PER_MINUTE:
                _register_abuse_strike(ip)
                return False, "请求过于频繁，请稍后再试"
            if day_count > IP_LIMIT_PER_DAY:
                _register_abuse_strike(ip)
                return False, "当日试用次数已达上限"
            return True, None
        except Exception:
            pass

    min_epoch, min_count = _mem_rate_minute.get(ip, (minute_epoch, 0))
    if min_epoch != minute_epoch:
        min_count = 0
    min_count += 1
    _mem_rate_minute[ip] = (minute_epoch, min_count)

    prev_day, day_count = _mem_rate_day.get(ip, (day_str, 0))
    if prev_day != day_str:
        day_count = 0
    day_count += 1
    _mem_rate_day[ip] = (day_str, day_count)

    if min_count > IP_LIMIT_PER_MINUTE:
        _register_abuse_strike(ip)
        return False, "请求过于频繁，请稍后再试"
    if day_count > IP_LIMIT_PER_DAY:
        _register_abuse_strike(ip)
        return False, "当日试用次数已达上限"
    return True, None


def _is_forced_captcha(ip: str) -> bool:
    now_ts = _now()

    if _redis is not None:
        try:
            return bool(_redis.get(_abuse_forced_key(ip)))
        except Exception:
            pass

    strikes, forced_until = _mem_abuse_state.get(ip, (0, 0.0))
    if forced_until <= now_ts:
        if strikes > 0:
            _mem_abuse_state[ip] = (strikes, 0.0)
        return False
    return True


def _register_abuse_strike(ip: str) -> None:
    now_ts = _now()

    if _redis is not None:
        try:
            strike_key = _abuse_strike_key(ip)
            forced_key = _abuse_forced_key(ip)

            strike_count = int(_redis.incr(strike_key))
            if strike_count == 1:
                _redis.expire(strike_key, ABUSE_STRIKE_TTL_SECONDS)

            if strike_count >= ABUSE_STRIKE_THRESHOLD:
                _redis.setex(forced_key, ABUSE_FORCE_CAPTCHA_SECONDS, "1")
                _redis.delete(strike_key)
            return
        except Exception:
            pass

    strikes, forced_until = _mem_abuse_state.get(ip, (0, 0.0))
    if forced_until > 0 and forced_until < now_ts:
        strikes = 0
        forced_until = 0.0

    strikes += 1
    if strikes >= ABUSE_STRIKE_THRESHOLD:
        _mem_abuse_state[ip] = (0, now_ts + ABUSE_FORCE_CAPTCHA_SECONDS)
    else:
        _mem_abuse_state[ip] = (strikes, forced_until)


def _current_rate_usage(ip: str) -> tuple[int, int]:
    now = int(time.time())
    minute_epoch = now // 60
    day_str = datetime.utcnow().strftime("%Y%m%d")

    if _redis is not None:
        try:
            minute_key = _rate_minute_key(ip, minute_epoch)
            day_key = _rate_day_key(ip, day_str)
            minute_count = int(_redis.get(minute_key) or 0)
            day_count = int(_redis.get(day_key) or 0)
            return minute_count, day_count
        except Exception:
            pass

    min_epoch, min_count = _mem_rate_minute.get(ip, (minute_epoch, 0))
    if min_epoch != minute_epoch:
        min_count = 0

    prev_day, day_count = _mem_rate_day.get(ip, (day_str, 0))
    if prev_day != day_str:
        day_count = 0

    return min_count, day_count


def _is_turnstile_mode_valid() -> bool:
    return TURNSTILE_MODE in {"always", "adaptive"}


def _turnstile_required(ip: str) -> bool:
    if not TURNSTILE_ENABLED:
        return False

    if not _is_turnstile_mode_valid():
        return True

    if TURNSTILE_MODE == "always":
        return True

    if _is_forced_captcha(ip):
        return True

    minute_count, day_count = _current_rate_usage(ip)
    return (
        _queue.qsize() >= TURNSTILE_RISK_QUEUE_THRESHOLD
        or minute_count >= TURNSTILE_RISK_MINUTE_THRESHOLD
        or day_count >= TURNSTILE_RISK_DAY_THRESHOLD
    )


async def _verify_turnstile(token: str, client_ip: str) -> tuple[bool, str | None]:
    if not TURNSTILE_ENABLED:
        return True, None

    if not TURNSTILE_SITE_KEY or not TURNSTILE_SECRET_KEY:
        return False, "服务端未完成验证码配置"

    if not token:
        return False, "请先完成验证码"

    def _do_verify() -> tuple[bool, str | None]:
        payload = urllib.parse.urlencode(
            {
                "secret": TURNSTILE_SECRET_KEY,
                "response": token,
                "remoteip": client_ip,
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            TURNSTILE_VERIFY_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if data.get("success") is True:
            return True, None

        error_codes = data.get("error-codes") or []
        if error_codes:
            return False, f"验证码校验失败: {','.join(error_codes)}"
        return False, "验证码校验失败"

    try:
        return await asyncio.to_thread(_do_verify)
    except Exception:
        return False, "验证码服务不可用，请稍后重试"


def _is_valid_device(value: str) -> bool:
    return value in {"auto", "gpu", "cpu"}


def _guess_suffix(filename: str | None, content_type: str | None) -> str:
    if filename:
        suffix = Path(filename).suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
            return suffix
    if content_type == "image/png":
        return ".png"
    if content_type == "image/webp":
        return ".webp"
    if content_type == "image/bmp":
        return ".bmp"
    return ".jpg"


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cleanup_task_map() -> None:
    cutoff = _now() - TASK_RETENTION_SECONDS
    remove_ids = [task_id for task_id, state in _tasks.items() if state.updated_at < cutoff]
    for task_id in remove_ids:
        _tasks.pop(task_id, None)


def _cache_set(task_id: str, result: dict[str, Any]) -> None:
    if _redis is None:
        return
    try:
        _redis.setex(_cache_key(task_id), CACHE_TTL_SECONDS, json.dumps(result, ensure_ascii=False))
    except Exception:
        pass


def _cache_get(task_id: str) -> dict[str, Any] | None:
    if _redis is None:
        return None
    try:
        value = _redis.get(_cache_key(task_id))
        if not value:
            return None
        return json.loads(value)
    except Exception:
        return None


def _get_detector(device: str) -> AIDetector:
    detector = _detector_pool.get(device)
    if detector is None:
        detector = AIDetector(device_mode=device)
        _detector_pool[device] = detector
    return detector


async def _worker_loop() -> None:
    while True:
        item = await _queue.get()
        if item is None:
            _queue.task_done()
            return

        task_id, device = item
        state = _tasks.get(task_id)
        if state is None:
            _queue.task_done()
            continue

        try:
            state.status = "running"
            state.updated_at = _now()

            if not state.file_path:
                raise RuntimeError("任务文件不存在")

            detector = _get_detector(device)
            details = detector.detect_detailed(state.file_path, verbose=False)

            state.status = "done"
            state.cached = False
            state.result = {
                "label": details["result"],
                "content_label": details.get("content_result", "未见明显AI内容"),
                "confidence": details["confidence"],
                "tags": details.get("tags", []),
            }
            state.updated_at = _now()
            _cache_set(task_id, state.result)
        except Exception as e:
            state.status = "failed"
            state.error = str(e)
            state.updated_at = _now()
        finally:
            if state.file_path:
                try:
                    Path(state.file_path).unlink(missing_ok=True)
                except Exception:
                    pass
                state.file_path = None
            _queue.task_done()


@app.on_event("startup")
async def _startup() -> None:
    global _redis, _worker_task
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    try:
        _redis = Redis.from_url(REDIS_URL, decode_responses=True)
        _redis.ping()
    except Exception:
        _redis = None

    _worker_task = asyncio.create_task(_worker_loop())


@app.on_event("shutdown")
async def _shutdown() -> None:
    global _worker_task, _shutting_down
    _shutting_down = True

    try:
        await asyncio.wait_for(_queue.join(), timeout=SHUTDOWN_GRACE_SECONDS)
    except asyncio.TimeoutError:
        pass

    for state in _tasks.values():
        if state.status in {"queued", "running"} and state.result is None:
            state.status = "failed"
            state.error = "服务正在关闭，任务终止"
            state.updated_at = _now()
        if state.file_path:
            try:
                Path(state.file_path).unlink(missing_ok=True)
            except Exception:
                pass
            state.file_path = None

    if _worker_task:
        await _queue.put(None)
        try:
            await asyncio.wait_for(_worker_task, timeout=5)
        except asyncio.TimeoutError:
            _worker_task.cancel()
            try:
                await _worker_task
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            pass

    _detector_pool.clear()
    cleanup_dire_runtime()


if WEB_BASE_PATH:
    def _root_redirect_to_base() -> RedirectResponse:
        return RedirectResponse(url=_route("/"))

    app.add_api_route("/", _root_redirect_to_base, methods=["GET"], include_in_schema=False)


@app.get(_route("/"), include_in_schema=False)
def index_page():
    return FileResponse(str(ROOT_DIR / "web_static" / "index.html"))


@app.get(_route("/static/index.html"), include_in_schema=False)
def legacy_index_redirect():
    return RedirectResponse(url=_route("/"))


@app.get(_route("/api/config/public"))
def public_config():
    return {
        "turnstile_enabled": bool(TURNSTILE_ENABLED),
        "turnstile_site_key": TURNSTILE_SITE_KEY if TURNSTILE_ENABLED else "",
        "turnstile_mode": TURNSTILE_MODE,
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "base_path": WEB_BASE_PATH,
        "api_base": _route("/api"),
        "static_base": _route("/static"),
    }


@app.get(_route("/api/abuse/check"))
def abuse_check(request: Request):
    trusted, payload = _jwt_context(request)
    bypass_allowed = trusted and _jwt_bypass_allowed(payload)
    if bypass_allowed:
        return {
            "turnstile_enabled": bool(TURNSTILE_ENABLED),
            "turnstile_mode": TURNSTILE_MODE,
            "require_captcha": False,
            "forced_captcha": False,
            "queue_size": _queue.qsize(),
            "minute_count": 0,
            "day_count": 0,
            "trusted": True,
            "bypass_allowed": True,
        }

    client_ip = _client_ip(request)
    minute_count, day_count = _current_rate_usage(client_ip)
    return {
        "turnstile_enabled": bool(TURNSTILE_ENABLED),
        "turnstile_mode": TURNSTILE_MODE,
        "require_captcha": _turnstile_required(client_ip),
        "forced_captcha": _is_forced_captcha(client_ip),
        "queue_size": _queue.qsize(),
        "minute_count": minute_count,
        "day_count": day_count,
        "trusted": trusted,
        "bypass_allowed": bypass_allowed,
    }


@app.post(_route("/api/tasks"))
async def create_task(
    request: Request,
    file: UploadFile = File(...),
    device: str = Form("auto"),
    captcha_token: str = Form(""),
):
    _cleanup_task_map()

    if _shutting_down:
        raise HTTPException(status_code=503, detail="服务正在关闭，请稍后重试")

    if not _origin_allowed(request):
        raise HTTPException(status_code=403, detail="来源不被允许")

    trusted, payload = _jwt_context(request)
    bypass_allowed = trusted and _jwt_bypass_allowed(payload)
    client_ip = _client_ip(request)
    need_captcha = _turnstile_required(client_ip)
    if not (bypass_allowed and JWT_BYPASS_CAPTCHA):
        if need_captcha and not captcha_token:
            raise HTTPException(status_code=428, detail="当前请求需要先完成验证码")
        if need_captcha:
            verified, verify_msg = await _verify_turnstile(captcha_token, client_ip)
            if not verified:
                raise HTTPException(status_code=400, detail=verify_msg)

    if not (bypass_allowed and JWT_BYPASS_RATE_LIMIT):
        allowed, message = _check_rate_limit(client_ip)
        if not allowed:
            raise HTTPException(status_code=429, detail=message)

    if _queue.qsize() >= MAX_QUEUE_SIZE:
        raise HTTPException(status_code=503, detail="当前任务繁忙，请稍后重试")

    device = (device or "auto").lower()
    if not _is_valid_device(device):
        raise HTTPException(status_code=400, detail="device 仅支持 auto/gpu/cpu")

    if file.content_type and not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="仅支持图片上传")

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="空文件")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"文件过大，最大 {MAX_UPLOAD_BYTES} 字节")

    task_id = _hash_bytes(payload)

    cached_result = _cache_get(task_id)
    if cached_result is not None:
        return {
            "task_id": task_id,
            "status": "done",
            "cached": True,
            "result": cached_result,
        }

    existing = _tasks.get(task_id)
    if existing is not None:
        data = {
            "task_id": task_id,
            "status": existing.status,
            "cached": existing.cached,
        }
        if existing.status == "done" and existing.result:
            data["result"] = existing.result
        if existing.status == "failed" and existing.error:
            data["error"] = existing.error
        return data

    suffix = _guess_suffix(file.filename, file.content_type)
    upload_path = UPLOAD_DIR / f"{task_id}{suffix}"
    upload_path.write_bytes(payload)

    now = _now()
    _tasks[task_id] = TaskState(
        task_id=task_id,
        status="queued",
        created_at=now,
        updated_at=now,
        device=device,
        file_path=str(upload_path),
    )
    await _queue.put((task_id, device))

    return {
        "task_id": task_id,
        "status": "queued",
        "cached": False,
    }


@app.get(_route("/api/tasks/{task_id}"))
def get_task(task_id: str):
    _cleanup_task_map()

    cached_result = _cache_get(task_id)
    if cached_result is not None:
        return {
            "task_id": task_id,
            "status": "done",
            "cached": True,
            "result": cached_result,
        }

    state = _tasks.get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    data = {
        "task_id": state.task_id,
        "status": state.status,
        "cached": state.cached,
        "device": state.device,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }
    if state.status == "done" and state.result is not None:
        data["result"] = state.result
    if state.status == "failed" and state.error:
        data["error"] = state.error
    return data


@app.get(_route("/api/tasks/{task_id}/result"))
def get_task_result(task_id: str):
    cached_result = _cache_get(task_id)
    if cached_result is not None:
        return {
            "task_id": task_id,
            "status": "done",
            "cached": True,
            "result": cached_result,
        }

    state = _tasks.get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    if state.status == "failed":
        raise HTTPException(status_code=400, detail=state.error or "任务失败")
    if state.status != "done" or state.result is None:
        raise HTTPException(status_code=409, detail=f"任务尚未完成: {state.status}")

    return {
        "task_id": task_id,
        "status": "done",
        "cached": state.cached,
        "result": state.result,
    }


@app.delete(_route("/api/tasks/{task_id}"))
def delete_task(task_id: str):
    state = _tasks.pop(task_id, None)
    if state and state.file_path:
        try:
            Path(state.file_path).unlink(missing_ok=True)
        except Exception:
            pass

    if _redis is not None:
        try:
            _redis.delete(_cache_key(task_id))
        except Exception:
            pass

    return {"ok": True, "task_id": task_id}


@app.get(_route("/api/health"))
def health():
    return {
        "ok": True,
        "base_path": WEB_BASE_PATH,
        "queue_size": _queue.qsize(),
        "redis": _redis is not None,
        "turnstile_enabled": bool(TURNSTILE_ENABLED),
    }


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="启动 Web 服务")
    parser.add_argument(
        "--host",
        default=os.getenv("WEB_HOST"),
        help="监听地址（默认: WEB_HOST 或 127.0.0.1）",
    )
    parser.add_argument("--public", action="store_true", help="公网监听（等价于 --host 0.0.0.0）")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("WEB_PORT", "8000")),
        help="监听端口（默认: WEB_PORT 或 8000）",
    )
    parser.add_argument("--reload", action="store_true", help="开发模式自动重载")
    args = parser.parse_args(argv)

    host = args.host or "127.0.0.1"
    if (args.public or _env_bool("WEB_PUBLIC", False)) and args.host is None:
        host = "0.0.0.0"

    reload_enabled = args.reload or _env_bool("WEB_RELOAD", False)
    if reload_enabled:
        uvicorn.run("web:app", host=host, port=args.port, reload=True)
    else:
        uvicorn.run(app, host=host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
