"""
Automated Kite Connect login (TOTP).
Replays the browser login flow against kite.zerodha.com, then completes the
Connect handshake to obtain a request_token and exchange it for an access_token.

This mirrors what a browser does during the official flow; Zerodha does not
publish it as an API, so if the login page changes this is the file to fix.
"""
import os, re, time, logging
from urllib.parse import urljoin

import pyotp, requests
from kiteconnect import KiteConnect

log = logging.getLogger("auth")

LOGIN_URL = "https://kite.zerodha.com/api/login"
TWOFA_URL = "https://kite.zerodha.com/api/twofa"
MAX_REDIRECTS = 10
REDIRECT_CODES = (301, 302, 303, 307, 308)


def _fresh_totp(secret: str, used: set) -> str:
    """Kite rejects a TOTP code that was already submitted, so a retry inside the
    same 30 s window fails again with "Invalid TOTP". Wait for the next window."""
    t = pyotp.TOTP(secret)
    code = t.now()
    if code in used:
        wait = 30 - (int(time.time()) % 30) + 1
        log.info("waiting %ds for a fresh TOTP window", wait)
        time.sleep(wait)
        code = t.now()
    used.add(code)
    return code


class KiteLoginRejected(RuntimeError):
    """Kite rejected the credentials or the 2FA code. Never retried: Zerodha locks
    the account after 5 wrong codes, so a second attempt with the same inputs
    only burns an attempt."""


def _kite_json(r: requests.Response, what: str) -> dict:
    """Kite answers 4xx with a JSON body whose 'message' says why (wrong user id,
    bad password, invalid TOTP...). Surface that instead of a bare HTTP status."""
    try:
        body = r.json()
    except ValueError:
        raise RuntimeError(f"{what}: HTTP {r.status_code}, non-JSON body {r.text[:200]!r}")
    if r.status_code >= 400 or body.get("status") != "success":
        msg = f"{what}: HTTP {r.status_code} {body.get('error_type', '')} {body.get('message') or body}"
        if 400 <= r.status_code < 500:
            raise KiteLoginRejected(msg)
        raise RuntimeError(msg)
    return body


def current_totp(secret: str) -> tuple[str, int]:
    """The 6-digit code this secret produces right now and the seconds it stays valid.
    Lets the preflight compare against the authenticator app before touching Kite."""
    return pyotp.TOTP(secret).now(), 30 - int(time.time()) % 30


def _browser_session(user_id: str, password: str, totp_secret: str, used_codes: set) -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (X11; Linux x86_64) scanradar-ingest"
    r = s.post(LOGIN_URL, data={"user_id": user_id, "password": password}, timeout=15)
    data = _kite_json(r, "login failed")["data"]
    twofa_type = pick_twofa_type(data)

    r = s.post(TWOFA_URL, data={"user_id": user_id, "request_id": data["request_id"],
                                "twofa_value": _fresh_totp(totp_secret, used_codes),
                                "twofa_type": twofa_type, "skip_session": "true"}, timeout=15)
    _kite_json(r, "2FA failed")
    return s


def pick_twofa_type(login_data: dict) -> str:
    """The /api/login response lists the second factors the account accepts, e.g.
    {"twofa_type": "app_code", "twofa_types": ["app_code", "sms"]}. Only "totp"
    (External 2FA TOTP enabled in Kite > My profile > Password & security) can be
    automated from a stored secret. Refuse up front otherwise: every rejected
    code counts toward Zerodha's 5-strike account lock."""
    available = login_data.get("twofa_types") or [login_data.get("twofa_type")]
    log.info("Kite 2FA for this account: type=%s available=%s status=%s",
             login_data.get("twofa_type"), available, login_data.get("twofa_status"))
    if "totp" not in available:
        raise KiteLoginRejected(
            f"External TOTP is not enabled on this Kite account (accepts {available}). Enable it in Kite web > "
            "My profile > Password & security > External 2FA TOTP, finish the verification step, then put the key "
            "shown there into .env (configure-env.sh). No 2FA attempt was made.")
    return "totp"


def probe_login(user_id: str, password: str) -> dict:
    """Password step only (does not count toward the 2FA lock): returns the login
    response data so the preflight can show the account's 2FA mode."""
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (X11; Linux x86_64) scanradar-ingest"
    r = s.post(LOGIN_URL, data={"user_id": user_id, "password": password}, timeout=15)
    return _kite_json(r, "login failed")["data"]


def fetch_request_token(api_key: str, sess: requests.Session) -> str:
    """Walk the Connect redirect chain one hop at a time and stop as soon as a hop
    carries request_token. The last hop is our own redirect URL
    (https://scanradar.tech/api/auth/callback), which may not resolve yet (DNS)
    or answers 401 behind Caddy basic auth. Letting requests follow redirects
    would raise ConnectionError before we ever saw the token."""
    url = KiteConnect(api_key=api_key).login_url()
    for _ in range(MAX_REDIRECTS):
        m = re.search(r"[?&]request_token=([A-Za-z0-9]+)", url)
        if m:
            return m.group(1)
        r = sess.get(url, allow_redirects=False, timeout=20)
        if r.status_code not in REDIRECT_CODES:
            path = url.split("?")[0]
            if path.endswith("/connect/authorize"):
                raise KiteLoginRejected(
                    "Kite is showing the one-time app authorisation page. Open "
                    f"https://kite.trade/connect/login?v=3&api_key={api_key} in a browser, log in, click Authorize "
                    "(the final redirect to scanradar.tech may 401/404; that is fine), then retry.")
            raise RuntimeError(f"Connect login stopped redirecting: HTTP {r.status_code} at {path} body={r.text[:200]!r}")
        url = urljoin(url, r.headers["Location"])
    raise RuntimeError("too many redirects without a request_token")


def session(R) -> KiteConnect:
    """Return a working KiteConnect, preferring the token cached in Redis.

    Kite issues one access_token per api_key: a fresh login invalidates the
    previous one, which would drop a WebSocket that is already streaming on it.
    So: reuse the cached token if Kite still accepts it; otherwise auto-login.
    Manual-token mode (KITE_PASSWORD empty): only the cached token is used
    (`redis-cli set kite:access_token <token>`)."""
    api_key = os.environ["KITE_API_KEY"]
    cached = R.get("kite:access_token")
    if cached:
        k = KiteConnect(api_key=api_key)
        k.set_access_token(cached)
        try:
            k.profile()
            log.info("reusing cached access token")
            return k
        except Exception as e:  # noqa: BLE001
            log.info("cached access token rejected (%s)", e)
    if not os.environ.get("KITE_PASSWORD"):
        raise RuntimeError("KITE_PASSWORD is empty (manual-token mode) and kite:access_token in Redis is missing or stale")
    k, token = login()
    R.set("kite:access_token", token, ex=20 * 3600)
    return k


def login(retries: int = 3) -> tuple[KiteConnect, str]:
    api_key, api_secret = os.environ["KITE_API_KEY"], os.environ["KITE_API_SECRET"]
    user_id, password, totp = os.environ["KITE_USER_ID"], os.environ["KITE_PASSWORD"], os.environ["KITE_TOTP_SECRET"]
    used_codes: set = set()
    last = None
    for attempt in range(1, retries + 1):
        try:
            sess = _browser_session(user_id, password, totp, used_codes)
            rt = fetch_request_token(api_key, sess)
            kite = KiteConnect(api_key=api_key)
            data = kite.generate_session(rt, api_secret=api_secret)
            kite.set_access_token(data["access_token"])
            log.info("Kite login OK for %s", data.get("user_id"))
            return kite, data["access_token"]
        except KiteLoginRejected as e:
            log.error("login rejected, not retrying (wrong credentials/TOTP would lock the account): %s", e)
            raise
        except Exception as e:  # noqa: BLE001   network / 5xx / parse problems: worth a retry
            last = e
            log.warning("login attempt %d failed: %s", attempt, e)
            time.sleep(10 * attempt)
    raise RuntimeError(f"Kite login failed after {retries} attempts: {last}")
