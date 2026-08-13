from __future__ import annotations

import hashlib
import hmac
import time
import uuid

from fastapi import Request

from .config import settings

COOKIE_NAME = "frontier_trusted_device"
INTERNAL_TOKEN_HEADER = "X-Frontier-Internal-Token"


def _signature(value: str) -> str:
    secret = hashlib.sha256(
        f"frontier-device-cookie:{settings.access_password}".encode()
    ).digest()
    return hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()


def create_device_token() -> str:
    expires_at = int(time.time()) + settings.trusted_device_days * 24 * 60 * 60
    value = f"{uuid.uuid4().hex}.{expires_at}"
    return f"{value}.{_signature(value)}"


def trusted_device(token: str | None) -> bool:
    if not settings.access_password:
        return True
    if not token:
        return False
    try:
        device_id, expires_at, signature = token.split(".", 2)
        value = f"{device_id}.{expires_at}"
        return (
            len(device_id) == 32
            and int(expires_at) >= int(time.time())
            and hmac.compare_digest(signature, _signature(value))
        )
    except (TypeError, ValueError):
        return False


def request_is_trusted(request: Request) -> bool:
    return trusted_device(request.cookies.get(COOKIE_NAME))


def password_matches(password: str) -> bool:
    return bool(settings.access_password) and hmac.compare_digest(
        password.encode(), settings.access_password.encode()
    )


def internal_api_token() -> str:
    if settings.internal_api_token:
        return settings.internal_api_token
    return hashlib.sha256(
        f"frontier-internal-api:{settings.access_password}".encode()
    ).hexdigest()


def request_is_internal(request: Request) -> bool:
    provided = request.headers.get(INTERNAL_TOKEN_HEADER)
    return bool(provided) and hmac.compare_digest(provided, internal_api_token())
