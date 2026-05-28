import types
import sys

import main


def test_main_dispatches_to_cli_module(monkeypatch):
    called = {"argv": None}

    def fake_cli_main(argv):
        called["argv"] = argv
        return 0

    fake_cli_module = types.SimpleNamespace(main=fake_cli_main)
    monkeypatch.setitem(sys.modules, "cli", fake_cli_module)

    rc = main.main(["--help"])

    assert rc == 0
    assert called["argv"] == ["--help"]
