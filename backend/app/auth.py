import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

from app.config import JWT_SECRET, TOKEN_HOURS


def hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return digest.hex()


def verify_password(password: str, salt: str, expected: str) -> bool:
    return hmac.compare_digest(hash_password(password, salt), expected)


def issue_token(user_id: str) -> str:
    payload = {
        "uid": user_id,
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS)).timestamp()),
    }
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    signature = hmac.new(JWT_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def read_token(token: str) -> str | None:
    try:
        body, signature = token.split(".", 1)
        expected = hmac.new(JWT_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
        if int(payload["exp"]) < int(datetime.now(timezone.utc).timestamp()):
            return None
        return payload["uid"]
    except (ValueError, KeyError, json.JSONDecodeError):
        return None
