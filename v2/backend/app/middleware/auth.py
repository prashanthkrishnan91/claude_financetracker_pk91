"""Authentication middleware — single-user/family model.

Simplified from multi-user RLS model:
  - Supabase Auth still handles login (email/password)
  - JWT validation ensures the request is authenticated
  - No RLS — both owner and family member see all data
  - user_id is still tracked for data ownership clarity
"""

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

    def __init__(self, user_id: UUID, email: str, role: str = "owner"):
        self.id = user_id
        self.email = email
        self.role = role  # "owner" or "family"


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedUser:
    """Validate the Supabase JWT and return the authenticated user.

    Single-user model: any valid JWT gets full access.
    No per-row authorization checks needed.
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
            algorithms=["HS256", "ES256"],
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

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing user ID",
        )

    return AuthenticatedUser(user_id=UUID(user_id), email=email, role="owner")
