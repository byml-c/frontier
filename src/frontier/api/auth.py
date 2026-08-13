from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..auth import COOKIE_NAME, create_device_token, password_matches, request_is_trusted
from ..config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=512)


@router.get("/status")
def auth_status(request: Request) -> dict:
    return {"trusted": request_is_trusted(request), "protection_enabled": bool(settings.access_password)}


@router.post("/login")
def login(payload: LoginRequest, response: Response) -> dict:
    if not password_matches(payload.password):
        raise HTTPException(status_code=401, detail="密码不正确")
    response.set_cookie(
        COOKIE_NAME,
        create_device_token(),
        max_age=settings.trusted_device_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.trusted_cookie_secure,
        samesite="lax",
        path="/",
    )
    return {"trusted": True}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"trusted": False}
