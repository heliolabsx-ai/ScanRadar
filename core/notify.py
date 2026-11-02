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
        log.warning("telegram send failed: %s", e)
        return False, str(e)
