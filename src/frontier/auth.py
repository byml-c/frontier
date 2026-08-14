from __future__ import annotations

import hashlib
import hmac
import uuid

from fastapi import Request, Response

from .config import settings

COOKIE_NAME = "frontier_trusted_device"
ANONYMOUS_COOKIE_NAME = "frontier_anonymous_device"
INTERNAL_TOKEN_HEADER = "X-Frontier-Internal-Token"
PERSISTENT_COOKIE_SECONDS = 10 * 365 * 24 * 60 * 60
ANONYMOUS_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
ANONYMOUS_COLORS = (
    "#1565C0", "#6A1B9A", "#00838F", "#2E7D32", "#AD1457",
    "#EF6C00", "#5D4037", "#455A64", "#3949AB", "#00897B",
)


def _signature(value: str, purpose: str = "trusted") -> str:
    secret = hashlib.sha256(
        f"frontier-device-cookie:{purpose}:{settings.access_password}".encode()
    ).digest()
    return hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()


def create_device_token() -> str:
    value = uuid.uuid4().hex
    return f"{value}.{_signature(value)}"


def trusted_device(token: str | None) -> bool:
    if not settings.access_password:
        return True
    if not token:
        return False
    try:
        parts = token.split(".")
        if len(parts) == 2:
            device_id, signature = parts
            return len(device_id) == 32 and hmac.compare_digest(signature, _signature(device_id))
        # Keep previously issued expiring cookies valid until the next login.
        if len(parts) == 3:
            device_id, expires_at, signature = parts
            value = f"{device_id}.{expires_at}"
            import time

            legacy_secret = hashlib.sha256(
                f"frontier-device-cookie:{settings.access_password}".encode()
            ).digest()
            expected = hmac.new(legacy_secret, value.encode(), hashlib.sha256).hexdigest()
            return len(device_id) == 32 and int(expires_at) >= int(time.time()) and hmac.compare_digest(signature, expected)
        return False
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


def _anonymous_token(device_id: str) -> str:
    return f"{device_id}.{_signature(device_id, 'anonymous')}"


def replace_anonymous_device(response: Response) -> str:
    device_id = uuid.uuid4().hex
    response.set_cookie(
        ANONYMOUS_COOKIE_NAME,
        _anonymous_token(device_id),
        max_age=PERSISTENT_COOKIE_SECONDS,
        httponly=True,
        secure=settings.trusted_cookie_secure,
        samesite="lax",
        path="/",
    )
    return device_id


def current_anonymous_device_id(request: Request) -> str | None:
    token = request.cookies.get(ANONYMOUS_COOKIE_NAME)
    if token:
        try:
            device_id, signature = token.split(".", 1)
            if len(device_id) == 32 and hmac.compare_digest(
                signature, _signature(device_id, "anonymous")
            ):
                return device_id
        except ValueError:
            pass
    return None


def anonymous_device_id(request: Request, response: Response) -> str:
    if device_id := current_anonymous_device_id(request):
        return device_id
    return replace_anonymous_device(response)


def anonymous_profile(device_id: str) -> dict[str, str]:
    digest = hmac.new(
        hashlib.sha256(f"frontier-anonymous:{settings.access_password}".encode()).digest(),
        device_id.encode(),
        hashlib.sha256,
    ).digest()
    name = "".join(ANONYMOUS_ALPHABET[value % len(ANONYMOUS_ALPHABET)] for value in digest[:4])
    return {"name": name, "color": ANONYMOUS_COLORS[digest[4] % len(ANONYMOUS_COLORS)]}
