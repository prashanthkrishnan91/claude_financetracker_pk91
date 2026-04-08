"""JWT authentication middleware — validates Supabase Auth tokens."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import Settings, get_settings

_bearer_scheme = HTTPBearer(auto_error=False)


class AuthenticatedUser:
    """Represents a validated authenticated user."""

    __slots__ = ("id", "email", "role")

    def __init__(self, user_id: UUID, email: str, role: str = "authenticated"):
        self.id = user_id
        self.email = email
        self.role = role


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedUser:
    """Validate the Supabase JWT and return the authenticated user.

    The JWT is issued by Supabase Auth on login/signup and contains:
    - sub: user UUID
    - email: user email
    - role: "authenticated"
    - exp: expiration timestamp

    Raises 401 if token is missing, expired, or invalid.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    try:
        payload = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    email = payload.get("email", "")
    role = payload.get("role", "authenticated")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing user ID",
        )

    return AuthenticatedUser(user_id=UUID(user_id), email=email, role=role)
