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
    monkeypatch.setattr(mod, "TOKEN", "t")
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
