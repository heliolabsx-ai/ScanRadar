"""Telegram deliveries: one row per send attempt, so the Alarms board can show whether a row rang."""
import datetime as dt


def record(con, day: dt.date, ref: str, channel: str, ok: bool, error: str | None, silent: bool, sent_at: dt.datetime) -> None:
    """Insert one delivery (the caller commits). channel: 'telegram' (the alarm's own message) or 'telegram-desk' (the desk ticket)."""
    with con.cursor() as cur:
        cur.execute("INSERT INTO deliveries(day, ref, channel, sent_at, ok, error, silent) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (day, ref, channel, sent_at, ok, error, silent))


def for_day(con, day: dt.date) -> dict[str, dict]:
    """ref -> the latest 'telegram' delivery of the day: {sent_at (iso), ok, error, silent}."""
    with con.cursor() as cur:
        cur.execute("SELECT ref, sent_at, ok, error, silent FROM deliveries WHERE day = %s AND channel = 'telegram' ORDER BY sent_at, id",
                    (day,))
        rows = cur.fetchall()
    return {ref: {"sent_at": sent_at.isoformat(), "ok": ok, "error": error, "silent": silent}
            for ref, sent_at, ok, error, silent in rows}
