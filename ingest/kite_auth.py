"""
Automated Kite Connect login (TOTP).
Replays the browser login flow against kite.zerodha.com, then completes the
Connect handshake to obtain a request_token and exchange it for an access_token.

This mirrors what a browser does during the official flow; Zerodha does not
publish it as an API, so if the login page changes this is the file to fix.
"""
import os, re, time, logging
from urllib.parse import urlparse, parse_qs

import pyotp, requests
from kiteconnect import KiteConnect

log = logging.getLogger("auth")

LOGIN_URL = "https://kite.zerodha.com/api/login"
TWOFA_URL = "https://kite.zerodha.com/api/twofa"
CONNECT_URL = "https://kite.trade/connect/login?v=3&api_key={api_key}"


def _browser_session(user_id: str, password: str, totp_secret: str) -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (X11; Linux x86_64) scanradar-ingest"
    r = s.post(LOGIN_URL, data={"user_id": user_id, "password": password}, timeout=15)
    r.raise_for_status()
    body = r.json()
    if body.get("status") != "success":
        raise RuntimeError(f"login failed: {body}")
    request_id = body["data"]["request_id"]

    totp = pyotp.TOTP(totp_secret).now()
    r = s.post(TWOFA_URL, data={"user_id": user_id, "request_id": request_id,
                                "twofa_value": totp, "twofa_type": "totp", "skip_session": "true"}, timeout=15)
    r.raise_for_status()
    body = r.json()
    if body.get("status") != "success":
        raise RuntimeError(f"2FA failed: {body}")
    return s


def fetch_request_token(api_key: str, sess: requests.Session) -> str:
    # Hitting the Connect login URL with a logged-in session bounces to the
    # app's redirect URL with ?request_token=... in the query string.
    r = sess.get(CONNECT_URL.format(api_key=api_key), allow_redirects=True, timeout=20)
    for h in r.history + [r]:
        q = parse_qs(urlparse(h.url).query)
        if "request_token" in q:
            return q["request_token"][0]
    # Redirect target may not resolve (DNS not live yet) - token still sits in the Location header.
    for h in r.history:
        loc = h.headers.get("Location", "")
        m = re.search(r"request_token=([A-Za-z0-9]+)", loc)
        if m:
            return m.group(1)
    raise RuntimeError(f"request_token not found; final url={r.url} status={r.status_code}")


def login(retries: int = 3) -> tuple[KiteConnect, str]:
    api_key, api_secret = os.environ["KITE_API_KEY"], os.environ["KITE_API_SECRET"]
    user_id, password, totp = os.environ["KITE_USER_ID"], os.environ["KITE_PASSWORD"], os.environ["KITE_TOTP_SECRET"]
    last = None
    for attempt in range(1, retries + 1):
        try:
            sess = _browser_session(user_id, password, totp)
            rt = fetch_request_token(api_key, sess)
            kite = KiteConnect(api_key=api_key)
            data = kite.generate_session(rt, api_secret=api_secret)
            kite.set_access_token(data["access_token"])
            log.info("Kite login OK for %s", data.get("user_id"))
            return kite, data["access_token"]
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("login attempt %d failed: %s", attempt, e)
            time.sleep(10 * attempt)
    raise RuntimeError(f"Kite login failed after {retries} attempts: {last}")
