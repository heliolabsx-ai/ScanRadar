import os, logging, requests
log = logging.getLogger("notify")
TOKEN, CHAT = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")


def telegram(text: str):
    if not (TOKEN and CHAT):
        log.info("telegram not configured; would send: %s", text)
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      json={"chat_id": CHAT, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
                      timeout=10).raise_for_status()
    except Exception as e:  # noqa
        log.warning("telegram send failed: %s", e)
