import os, logging, requests
log = logging.getLogger("notify")
TOKEN, CHAT = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")


def telegram(text: str, silent: bool = False) -> tuple[bool, str | None]:
    """Send one message; returns (ok, error): (True, None) on success, (False, "not configured") without token/chat, (False, reason) on a failed send."""
    if not (TOKEN and CHAT):
        log.info("telegram not configured; would send: %s", text)
        return False, "not configured"
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      json={"chat_id": CHAT, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True,
                            "disable_notification": silent},
                      timeout=10).raise_for_status()
        return True, None
    except Exception as e:  # noqa
        err = _safe_error(e)
        log.warning("telegram send failed: %s", err)
        return False, err


def _safe_error(e: Exception) -> str:
    """The failure as stored and logged, never containing the bot token (requests puts the request URL, token included, in str(e))."""
    resp = getattr(e, "response", None)
    if resp is not None:                  # a Response is falsy on an error status: test for None
        desc = None
        try:
            desc = resp.json().get("description")
        except Exception:  # noqa
            pass
        err = f"{getattr(resp, 'status_code', '?')} {desc or getattr(resp, 'reason', '') or ''}".strip()
    else:
        err = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
    return err.replace(TOKEN, "<token>") if TOKEN else err
