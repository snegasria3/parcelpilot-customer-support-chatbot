from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

import bcrypt

from backend.database import Database
from backend.schemas import CustomerIdentity

COOKIE_NAME = "pp_customer_session"
DUMMY_BCRYPT_HASH = b"$2b$12$AcdjKneH3JQjFLsae6NFYeJ/mF3RETP24MRxCmG81rgIoButQj4z6"


class AuthenticationError(Exception):
    """Raised when customer authentication fails."""


class AuthorizationError(Exception):
    """Raised when an authenticated customer cannot perform an operation."""


class ScopedRecordNotFound(Exception):
    """Hides whether a record is absent or belongs to another customer."""


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))


@dataclass(slots=True)
class SessionTokenService:
    secret: str
    ttl_minutes: int

    def issue(self, identity: CustomerIdentity) -> tuple[str, str, int]:
        now = int(time.time())
        ttl_seconds = self.ttl_minutes * 60
        csrf_token = secrets.token_urlsafe(24)
        payload = {
            "sub": identity.user_id,
            "account_id": identity.account_id,
            "username": identity.username,
            "iat": now,
            "exp": now + ttl_seconds,
            "jti": secrets.token_urlsafe(12),
            "csrf": csrf_token,
        }
        encoded = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        signature = _b64encode(hmac.new(self.secret.encode(), encoded.encode(), hashlib.sha256).digest())
        return f"{encoded}.{signature}", csrf_token, ttl_seconds

    def verify(self, token: str) -> dict[str, Any]:
        try:
            encoded, received = token.split(".", maxsplit=1)
            expected = _b64encode(hmac.new(self.secret.encode(), encoded.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(received, expected):
                raise AuthenticationError("Invalid session")
            payload = json.loads(_b64decode(encoded))
            if int(payload["exp"]) <= int(time.time()):
                raise AuthenticationError("Session expired")
            for key in ("sub", "account_id", "username", "jti", "csrf"):
                if not payload.get(key):
                    raise AuthenticationError("Invalid session")
            return payload
        except AuthenticationError:
            raise
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise AuthenticationError("Invalid session") from exc


class LoginRateLimiter:
    def __init__(self, limit: int, window_seconds: int = 60):
        self.limit = limit
        self.window_seconds = window_seconds
        self._attempts: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            attempts = self._attempts[key]
            while attempts and now - attempts[0] > self.window_seconds:
                attempts.popleft()
            if len(attempts) >= self.limit:
                raise AuthenticationError("Too many login attempts. Please wait one minute.")
            attempts.append(now)

    def clear(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)


def authenticate_customer(db: Database, username: str, password: str) -> CustomerIdentity:
    encoded_password = password.encode("utf-8")
    if len(encoded_password) > 72:
        raise AuthenticationError("Invalid customer ID or password")
    row = db.fetch_one(
        "SELECT * FROM customer_users WHERE lower(username) = lower(?) AND is_active = 1",
        (username.strip(),),
    )
    password_hash = row["password_hash"].encode() if row else DUMMY_BCRYPT_HASH
    try:
        valid = bcrypt.checkpw(encoded_password, password_hash)
    except ValueError:
        valid = False
    if row is None or not valid:
        raise AuthenticationError("Invalid customer ID or password")
    return CustomerIdentity(
        user_id=row["user_id"],
        username=row["username"],
        display_name=row["display_name"],
        account_id=row["account_id"],
    )


def identity_from_session(db: Database, service: SessionTokenService, token: str) -> tuple[CustomerIdentity, str]:
    payload = service.verify(token)
    row = db.fetch_one(
        "SELECT * FROM customer_users WHERE user_id = ? AND account_id = ? AND is_active = 1",
        (payload["sub"], payload["account_id"]),
    )
    if row is None:
        raise AuthenticationError("Customer session is no longer active")
    identity = CustomerIdentity(
        user_id=row["user_id"],
        username=row["username"],
        display_name=row["display_name"],
        account_id=row["account_id"],
        token_id=payload["jti"],
    )
    return identity, str(payload["csrf"])


def require_csrf(expected: str, received: str | None) -> None:
    if not received or not hmac.compare_digest(expected, received):
        raise AuthorizationError("Missing or invalid confirmation token")
