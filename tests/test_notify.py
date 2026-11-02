# tests/test_notify.py
import importlib, sys, types


def _notify(monkeypatch):
    """core.notify imported against a stub `requests` (the dev venv does not carry it); returns (module, bodies posted)."""
    sent = []

    class Resp:
        def raise_for_status(self): pass

    stub = types.ModuleType("requests")
    stub.post = lambda url, json=None, timeout=None: sent.append(json) or Resp()
    monkeypatch.setitem(sys.modules, "requests", stub)
    monkeypatch.delitem(sys.modules, "core.notify", raising=False)
    mod = importlib.import_module("core.notify")
    monkeypatch.setattr(mod, "TOKEN", "123:TESTTOKEN")
    monkeypatch.setattr(mod, "CHAT", "c")
    return mod, sent


def test_telegram_is_loud_by_default(monkeypatch):
    mod, sent = _notify(monkeypatch)
    mod.telegram("x")
    assert sent[0]["disable_notification"] is False and sent[0]["text"] == "x"


def test_telegram_silent_sets_disable_notification(monkeypatch):
    mod, sent = _notify(monkeypatch)
    mod.telegram("x", silent=True)
    assert sent[0]["disable_notification"] is True


def test_telegram_returns_ok(monkeypatch):
    mod, sent = _notify(monkeypatch)
    assert mod.telegram("x") == (True, None)


def test_telegram_not_configured(monkeypatch):
    mod, sent = _notify(monkeypatch); monkeypatch.setattr(mod, "TOKEN", None)
    assert mod.telegram("x") == (False, "not configured") and sent == []


def test_telegram_reports_the_error(monkeypatch):
    mod, sent = _notify(monkeypatch)
    def boom(url, json=None, timeout=None): raise RuntimeError("timeout")
    monkeypatch.setattr(mod.requests, "post", boom)
    assert mod.telegram("x") == (False, "RuntimeError: timeout")


def test_telegram_error_never_contains_the_token(monkeypatch, caplog):
    mod, sent = _notify(monkeypatch)
    monkeypatch.setattr(mod, "TOKEN", "123:SECRET")
    def boom(url, json=None, timeout=None): raise RuntimeError(f"HTTPSConnectionPool: url: /bot{mod.TOKEN}/sendMessage failed")
    monkeypatch.setattr(mod.requests, "post", boom)
    with caplog.at_level("WARNING", logger="notify"):
        ok, err = mod.telegram("x")
    assert ok is False and "123:SECRET" not in err and "<token>" in err
    assert "123:SECRET" not in caplog.text and "telegram send failed" in caplog.text


def test_telegram_http_error_uses_telegrams_description(monkeypatch, caplog):
    mod, sent = _notify(monkeypatch)
    monkeypatch.setattr(mod, "TOKEN", "123:SECRET")

    class Resp:
        status_code, reason = 401, "Unauthorized"
        def __bool__(self): return False
        def json(self): return {"description": "Unauthorized"}

    class HTTPError(Exception):
        response = Resp()

    def boom(url, json=None, timeout=None): raise HTTPError(f"401 Client Error for url: https://api.telegram.org/bot{mod.TOKEN}/sendMessage")
    monkeypatch.setattr(mod.requests, "post", boom)
    with caplog.at_level("WARNING", logger="notify"):
        assert mod.telegram("x") == (False, "401 Unauthorized")
    assert "123:SECRET" not in caplog.text


def test_telegram_http_error_without_json_body_uses_the_reason(monkeypatch):
    mod, sent = _notify(monkeypatch)

    class Resp:
        status_code, reason = 502, "Bad Gateway"
        def json(self): raise ValueError("no json")

    class HTTPError(Exception):
        response = Resp()

    def boom(url, json=None, timeout=None): raise HTTPError("x")
    monkeypatch.setattr(mod.requests, "post", boom)
    assert mod.telegram("x") == (False, "502 Bad Gateway")
