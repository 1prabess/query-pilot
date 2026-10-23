import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi.concurrency import run_in_threadpool
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

from app.core.config import get_settings

# recommened() defaults to argon2
_password_hash = PasswordHash.recommended()

# used for nonexistent accounts to ensure password verification still runs which helps preventing attackers from identifying registered emails through response timing.
_DUMMY_HASH = _password_hash.hash("a password that belongs to no user")

settings = get_settings()


async def hash_password(password: str) -> str:
    return await run_in_threadpool(_password_hash.hash, password)


async def verify_password(stored_hash: str | None, supplied: str) -> bool:
    candidate = stored_hash or _DUMMY_HASH

    try:
        verified = await run_in_threadpool(_password_hash.verify, supplied, candidate)
    except UnknownHashError:
        return False
    return bool(verified and stored_hash)


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def create_access_token(
        user_id: str,
        *,
        expires_delta: timedelta | None = None
) -> tuple[str, datetime]:

    now = datetime.now(timezone.utc)

    expires = now + \
        (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_TTL_MINUTES))
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": expires
    }

    return jwt.encode(
        payload,
        key=settings.SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM
    ), expires


def decode_access_token(token: str) -> str:
    payload = jwt.decode(
        token,
        key=settings.SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        options={"require": ["exp", "sub"]},
    )

    return str(payload["sub"])
