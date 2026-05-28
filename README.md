# AI 图像检测工具

一个支持本地批量检测与在线服务的 AI 图像检测项目。

## 免责声明

- 本工具输出结果仅供参考，不保证准确性、完整性或适用于任何特定用途。
- 对于压缩、转码、二次截图、局部嵌入、复杂编辑等场景，误报和漏报风险会明显上升。
- 涉及证件审核、内容合规、司法取证、风控封禁等高风险决策时，请务必结合人工复核与多源证据，不可仅依赖本工具结论。

项目提供两种工作模式：
- CLI 模式：本地命令行检测单图或目录
- Web 模式：上传图片后异步检测（带 hash 缓存命中）

## 特性

- 支持单图检测与目录批量检测
- 支持推理设备切换：`auto` / `gpu` / `cpu`
- 目录检测可生成 HTML 报告，支持多模板
- Web 服务支持 API + 静态前端同进程托管
- 上传内容按 `sha256` 作为任务 ID，可直接缓存命中
- 支持优雅退出（停止接单、等待队列、清理临时文件）

## 项目结构

```text
main.py                  # 统一入口
requirements.txt         # 基础依赖（CLI）
web_requirements.txt     # Web 依赖（基础依赖 + FastAPI 等）
Dockerfile               # 默认容器构建
src/
  cli.py                 # CLI 检测逻辑
  web.py                 # Web API + 静态资源服务
  ai_detector/           # 检测核心
templates/reports/       # HTML 报告模板
web_static/              # 前端静态资源
```

## 环境要求

- Python 3.11
- 建议使用虚拟环境（如 `.venv`）

## 安装

### 1) CLI 模式依赖

```bash
python -m pip install -r requirements.txt
```

### 2) Web 模式依赖

```bash
python -m pip install -r web_requirements.txt
```

## 使用说明

### 模式一：CLI 检测模式

统一入口默认走 CLI：

```bash
python main.py --help
```

#### 单图检测

```bash
python main.py path/to/image.jpg --device auto
```

#### 目录批量检测

```bash
python main.py -d ./demo --device gpu --template default
```

#### CLI 参数

- `image_path`：单图路径
- `-d, --dir`：目录批量检测
- `--device {auto,gpu,cpu}`：设备模式
- `-t, --template`：报告模板名或模板路径（目录模式生效）
- `-q, --quiet`：静默模式

#### 报告模板

默认模板目录：`templates/reports/`

示例：

```bash
python main.py -d ./demo -t compact
```

### 模式二：Web 服务模式

通过子命令启动 Web：

```bash
python main.py web --help
```

常用启动方式：

```bash
# 本机监听（默认）
python main.py web

# 公网监听
python main.py web --public

# 指定端口
python main.py web --port 8000
```

默认访问地址：

- 根路径：`http://127.0.0.1:8000/`
- 旧地址 `.../static/index.html` 仍兼容，会自动跳回根路径

#### Web 接口

- `GET /`：前端页面
- `GET /api/health`：健康检查
- `POST /api/tasks`：上传并创建任务
- `GET /api/tasks/{task_id}`：查询任务状态
- `GET /api/tasks/{task_id}/result`：获取任务结果
- `DELETE /api/tasks/{task_id}`：删除任务/缓存

#### 任务与缓存机制

- 上传文件计算 `sha256` 作为 `task_id`
- 如果 Redis 中已有该 `task_id` 结果，直接返回缓存结果
- 首次提交进入队列异步处理
- 检测完成后删除临时上传文件

## 环境变量

### Web 服务配置

- `WEB_HOST`：默认监听地址（默认 `127.0.0.1`）
- `WEB_PORT`：默认端口（默认 `8000`）
- `WEB_PUBLIC`：是否公网监听（`1/true/yes/on`）
- `WEB_RELOAD`：是否自动重载（开发环境）
- `WEB_BASE_PATH`：部署基础路径前缀（默认空，即根路径）。示例：`/ai-detector`
- `WEB_REDIS_URL`：Redis 地址（默认 `redis://127.0.0.1:6379/0`）
- `WEB_MAX_UPLOAD_BYTES`：上传大小上限（默认 20MB）
- `WEB_CACHE_TTL_SECONDS`：结果缓存 TTL（默认 1800 秒）
- `WEB_TASK_RETENTION_SECONDS`：内存任务保留时长（默认 3600 秒）
- `WEB_SHUTDOWN_GRACE_SECONDS`：优雅退出等待时长（默认 20 秒）
- `WEB_UPLOAD_DIR`：上传临时目录（默认系统临时目录下 `ai-detector-web-uploads`）
- `WEB_MAX_QUEUE_SIZE`：队列上限（默认 128）
- `WEB_IP_LIMIT_PER_MINUTE`：单 IP 每分钟提交上限（默认 20）
- `WEB_IP_LIMIT_PER_DAY`：单 IP 每日提交上限（默认 300）
- `WEB_ALLOWED_ORIGINS`：允许的浏览器 Origin 白名单（逗号分隔，可为空）
- `WEB_TURNSTILE_ENABLED`：是否启用 Turnstile（`1/true/yes/on`）
- `WEB_TURNSTILE_SITE_KEY`：Turnstile 前端 site key
- `WEB_TURNSTILE_SECRET_KEY`：Turnstile 服务端 secret key
- `WEB_TURNSTILE_VERIFY_URL`：Turnstile 校验地址（默认 Cloudflare 官方地址）
- `WEB_TURNSTILE_MODE`：验证码模式（`adaptive`/`always`，默认 `adaptive`）
- `WEB_TURNSTILE_RISK_MINUTE_THRESHOLD`：触发验证码的分钟请求阈值
- `WEB_TURNSTILE_RISK_DAY_THRESHOLD`：触发验证码的日请求阈值
- `WEB_TURNSTILE_RISK_QUEUE_THRESHOLD`：触发验证码的队列长度阈值
- `WEB_ABUSE_STRIKE_THRESHOLD`：触发强制验证码前的违规次数阈值（默认 3）
- `WEB_ABUSE_STRIKE_TTL_SECONDS`：违规计数窗口秒数（默认 900）
- `WEB_ABUSE_FORCE_CAPTCHA_SECONDS`：进入强制验证码窗口时长（默认 1800）
- `WEB_JWT_ENABLED`：是否启用 JWT 鉴权（`1/true/yes/on`）
- `WEB_JWT_SECRET`：JWT 签名密钥（建议 32+ 字符随机串）
- `WEB_JWT_ALGORITHMS`：允许算法列表（逗号分隔，默认 `HS256`）
- `WEB_JWT_BYPASS_RATE_LIMIT`：JWT 请求是否跳过 IP 频控（默认 `1`）
- `WEB_JWT_BYPASS_CAPTCHA`：JWT 请求是否跳过验证码（默认 `1`）
- `WEB_JWT_REQUIRE_BYPASS_CLAIM`：旁路是否强制要求 claim（默认 `1`）
- `WEB_JWT_BYPASS_CLAIM_KEY`：用于旁路判断的 claim 键（默认 `role`）
- `WEB_JWT_BYPASS_CLAIM_VALUE`：用于旁路判断的 claim 值（默认 `internal`）

### JWT 无限制调用（服务端到服务端）

当你希望“自己调用不受匿名限流影响”时，可启用 JWT：

1. 配置环境变量

```bash
WEB_JWT_ENABLED=1
WEB_JWT_SECRET=请替换为高强度随机密钥
WEB_JWT_ALGORITHMS=HS256
WEB_JWT_BYPASS_RATE_LIMIT=1
WEB_JWT_BYPASS_CAPTCHA=1
WEB_JWT_REQUIRE_BYPASS_CLAIM=1
WEB_JWT_BYPASS_CLAIM_KEY=role
WEB_JWT_BYPASS_CLAIM_VALUE=internal
```

2. 生成 token（示例，Python）

```python
import time
import jwt

secret = "请替换为与 WEB_JWT_SECRET 一致的值"
payload = {
    "sub": "internal-client",
  "role": "internal",
    "iat": int(time.time()),
    "exp": int(time.time()) + 3600,
}
token = jwt.encode(payload, secret, algorithm="HS256")
print(token)
```

3. 调用 API 时附带 Bearer Token

```bash
curl -X POST http://127.0.0.1:8000/api/tasks \
  -H "Authorization: Bearer <你的token>" \
  -F "file=@./demo/example.jpg" \
  -F "device=auto"
```

说明：

- 未携带 token 的请求仍按匿名策略执行（限流/验证码）
- 携带有效 token 但不满足 claim（默认 `role=internal`）时，仍按匿名策略执行
- 只有“有效 token + 满足 claim + bypass 开关开启”才会跳过对应限制
- 队列上限 `WEB_MAX_QUEUE_SIZE` 仍生效，用于保护服务稳定性

## Docker 部署

项目已包含默认 `Dockerfile`。

### 构建镜像

```bash
docker build -t ai-detector-web .
```

### 运行容器

```bash
docker run --rm -p 8000:8000 \
  -e WEB_REDIS_URL=redis://redis:6379/0 \
  ai-detector-web
```

说明：
- Dockerfile 默认设置 `WEB_PUBLIC=1`，容器内会监听 `0.0.0.0`
- 建议在生产中通过 Nginx 反向代理到该服务

## 优雅退出

Web 服务在停止时会执行：

- 拒绝新任务提交
- 在超时时间内等待队列处理完成
- 超时后中止未完成任务并标记失败
- 清理临时上传文件与推理资源

## 开发建议

- 新增报告样式时，优先在 `templates/reports/` 添加模板
- 线上建议开启 Redis，确保 hash 缓存生效
- 若需要更高吞吐，建议后续拆分独立 worker 进程

## License

本项目使用 MIT License，详见 `LICENSE` 文件。

## 部署附录（生产建议）

本节提供可直接参考的 Nginx 与 Compose 模板，示例文件已放在：

- `deploy/docker-compose.web.example.yml`
- `deploy/nginx.ai-detector.example.conf`

### 一、Docker Compose（接入现有网络）

如果你的 Redis 与 Nginx 已在同一个外部网络（例如 `app-net`），可直接复用：

1. 先构建镜像

```bash
docker build -t ai-detector-web:latest .
```

2. 使用示例 compose 启动

```bash
docker compose -f deploy/docker-compose.web.example.yml up -d
```

重点参数：

- `WEB_REDIS_URL`：改为你当前 Redis 服务地址
- `WEB_UPLOAD_DIR`：可选，建议不配置（默认使用临时目录）
- `WEB_MAX_UPLOAD_BYTES`：上传大小上限
- `WEB_CACHE_TTL_SECONDS`：结果缓存时长
- `WEB_SHUTDOWN_GRACE_SECONDS`：优雅退出等待时长

### 二、Nginx 反向代理

将 `deploy/nginx.ai-detector.example.conf` 合并到你的 Nginx 配置中，重点保留：

- `client_max_body_size`：限制上传体积
- `proxy_read_timeout`：适配检测耗时
- `X-Forwarded-*`：保留真实来源信息

建议在网关层开启：

- `limit_req`（限流）
- 基础防刷策略（按 IP/UA）

#### 子路径部署示例（主域名某目录）

如果你希望挂在 `https://example.com/ai-detector/`：

1. Web 服务设置：

```bash
WEB_BASE_PATH=/ai-detector
```

2. Nginx 使用同样前缀反代（不要去掉前缀）：

```nginx
location /ai-detector/ {
  proxy_http_version 1.1;
  proxy_set_header Host $host;
  proxy_set_header X-Real-IP $remote_addr;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;
  proxy_set_header Connection "";
  proxy_pass http://ai_detector_web;
}

location = /ai-detector/api/tasks {
  limit_req zone=api_limit burst=20 nodelay;

  proxy_http_version 1.1;
  proxy_set_header Host $host;
  proxy_set_header X-Real-IP $remote_addr;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;
  proxy_set_header Connection "";
  proxy_pass http://ai_detector_web;
}
```

说明：

- 二级域名独立部署时，保持 `WEB_BASE_PATH` 为空即可
- 子路径部署时，前端会自动按当前路径前缀拼接 API 地址

### 匿名试用防滥用（推荐基线）

当前项目已内置以下防护：

- API 层单 IP 频率限制（分钟/日维度）
- 任务队列上限控制（超载返回 503）
- 可选 Origin 白名单校验
- 上传 hash 缓存命中（重复图片不重复算）

建议配置：

- Nginx 与 API 双层限流同时开启
- 开启 `WEB_ALLOWED_ORIGINS`（例如你的前端域名）
- 观察 `429/503` 比例并逐步调优阈值

如果后续仍被脚本滥用，可在此基础上增加：

- 人机验证（Turnstile/hCaptcha）
- 按 IP 指纹灰度封禁
- 针对 `/api/tasks` 的动态挑战（仅高风险触发）

### Turnstile 启用步骤（推荐）

1. 在 Cloudflare Turnstile 创建站点，获取 `site key` 和 `secret key`
2. 配置环境变量：

```bash
WEB_TURNSTILE_ENABLED=1
WEB_TURNSTILE_SITE_KEY=你的_site_key
WEB_TURNSTILE_SECRET_KEY=你的_secret_key
```

3. 重启服务后，前端会自动加载验证码组件
4. 上传接口 `/api/tasks` 会校验 token，未通过将返回 `400`

说明：

- `WEB_TURNSTILE_MODE=adaptive` 时，仅在高风险（高频/高队列）场景要求验证码
- 可通过 `/api/abuse/check` 查看当前请求是否需要验证码
- 连续触发限流后，IP 会进入一段时间的强制验证码窗口（灰度策略）

### 三、健康检查与可观测性

当前服务提供：

- `GET /api/health`

建议：

- 容器健康检查直接访问 `http://127.0.0.1:8000/api/health`
- 监控指标至少覆盖：
  - 请求成功率
  - 队列长度（`queue_size`）
  - 任务失败率
  - 平均检测耗时

### 四、日志建议

建议把日志分成两类：

- 访问日志（Nginx）
- 应用日志（web 服务）

生产实践：

- Nginx 开启按日轮转
- 应用日志写 stdout/stderr，由容器平台采集
- 出现异常任务时，记录 `task_id`、`device`、错误原因

### 五、上线核对清单

- Redis 连通（`WEB_REDIS_URL` 可达）
- Nginx 反代到 web 容器成功
- 上传大小限制与 Nginx 保持一致
- `WEB_PUBLIC=1`（容器内对外监听）
- `WEB_SHUTDOWN_GRACE_SECONDS` 已按业务时延调优
