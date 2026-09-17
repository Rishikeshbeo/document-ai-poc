"""The no-login handshake.

The host app already knows who its user is. It calls us server-to-server with
its API key and the company ID of the logged-in user. We hand back a
short-lived token. The iframe URL carries that token, so the panel knows the
application and the company without ever asking the user to sign in.

Stdlib HMAC rather than a JWT library — one less dependency, same guarantees
for a POC. Swap for a real JWT with rotating keys before production.
"""

import base64
import hashlib
import hmac
import json
import os
import time

from . import db

SECRET = os.environ.get("DOCAI_SECRET", "poc-secret-change-me").encode()
TTL_SECONDS = 30 * 60

ADMIN_USER = os.environ.get("DOCAI_ADMIN_USER", "user1")

# Deliberately trivial, so the demo is not a password hunt. It is a door that
# is shut rather than a door that is locked: fine on a laptop, wrong the moment
# the service is reachable by anyone else. Set DOCAI_ADMIN_PASSWORD then.
DEFAULT_ADMIN_PASSWORD = "1234"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def mint(app_id: str, company_id: str, user_ref: str = "") -> str:
    payload = {
        "app": app_id,
        "company": company_id,
        "user": user_ref,          # opaque string from the host, never a name
        "exp": int(time.time()) + TTL_SECONDS,
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


class TokenError(Exception):
    pass


def verify(token: str) -> dict:
    try:
        body, sig = token.split(".")
    except ValueError:
        raise TokenError("Token is malformed.")
    expected = _b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise TokenError("Token signature does not match.")
    payload = json.loads(_unb64(body))
    if payload["exp"] < time.time():
        raise TokenError("Token has expired. The host app should request a new one.")
    return payload


# --- superadmin -------------------------------------------------------------

def admin_password() -> str:
    """The password for the superadmin area.

    `DOCAI_ADMIN_PASSWORD` wins, so a real deployment never uses the default.
    Otherwise the stored value, which is the demo default until someone changes
    it. Either way the area is never open.
    """
    env = os.environ.get("DOCAI_ADMIN_PASSWORD", "").strip()
    if env:
        return env
    stored = db.get_setting("admin_password")
    if not stored:
        stored = db.set_setting("admin_password", DEFAULT_ADMIN_PASSWORD)
    return stored


def admin_is_generated() -> bool:
    return not os.environ.get("DOCAI_ADMIN_PASSWORD", "").strip()


def check_admin(authorization: str) -> bool:
    """HTTP Basic. The browser keeps the credentials, so the admin pages need
    no login form and their fetch() calls carry the header on their own."""
    if not authorization or not authorization.lower().startswith("basic "):
        return False
    try:
        raw = base64.b64decode(authorization.split(" ", 1)[1].strip()).decode("utf-8", "replace")
    except (ValueError, IndexError):
        return False
    user, _, password = raw.partition(":")
    # Both compared before returning, so a wrong username costs the same as a
    # wrong password.
    user_ok = hmac.compare_digest(user.encode(), ADMIN_USER.encode())
    pass_ok = hmac.compare_digest(password.encode(), admin_password().encode())
    return user_ok and pass_ok
