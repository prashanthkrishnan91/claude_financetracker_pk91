"""Authentication middleware - single-user/family model.

Simplified from multi-user RLS model:
- Supabase Auth still handles login (email/password)
- JWT validation ensures the request is authenticated
- No RLS - both owner and family member see all data
- user_id is still tracked for data ownership clarity
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

import jwt
from jwt import PyJWKClient, PyJWKClientError
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
        self.role = role # "owner" or "family"

# Cache the client at the module level so it doesn't re-fetch the JWKS on every request
_jwks_client: Optional[PyJWKClient] = None

def get_jwks_client(supabase_url: str) -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        jwks_url = f"{supabase_url}/auth/v1/.well-known/jwks.json"
        _jwks_client = PyJWKClient(jwks_url)
    return _jwks_client

async def get_current_user(
    request: Request,
    auth: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedUser:
    """
    Validate the Supabase JWT using dynamic JWKS and return the authenticated user.
    """
    if not auth:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth.credentials
    jwks_client = get_jwks_client(settings.supabase_url)

    try:
        # Dynamically fetch the correct signing key directly from Supabase
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        # Decode the payload using the dynamic key (handles ES256 signatures automatically)
        payload = jwt.decode(
            token,
            key=signing_key.key,
            algorithms=["RS256", "ES256", "HS256"],
            audience="authenticated",
            options={"verify_exp": True, "verify_aud": True}
        )

        user_id_str = payload.get("sub")
        email = payload.get("email", "")
        
        # Safely get the role if it exists in app_metadata, default to 'owner'
        app_metadata = payload.get("app_metadata", {})
        role = app_metadata.get("role", "owner") if isinstance(app_metadata, dict) else "owner"

        if not user_id_str:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing subject (user ID)"
            )

        return AuthenticatedUser(
            user_id=UUID(user_id_str),
            email=email,
            role=role
        )

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired"
        )
    except (jwt.InvalidTokenError, PyJWKClientError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}"
        )


async def get_current_user_from_request(request: Request) -> AuthenticatedUser:
    """
    Resolve the authenticated user from a raw Request outside of FastAPI's
    own dependency-injection call path.

    get_current_user's `auth` and `settings` parameters are declared with
    Depends(...) defaults that only resolve when FastAPI itself calls the
    function as part of a route's dependency graph. Calling
    get_current_user(request) directly (e.g. from another dependency that
    already holds a validated Request) leaves those parameters as
    unresolved Depends objects and crashes with
    `AttributeError: 'Depends' object has no attribute 'credentials'`.

    This helper explicitly resolves the bearer credentials via the same
    HTTPBearer scheme and the settings singleton, then delegates to
    get_current_user's existing JWT validation logic unchanged.
    """
    auth = await _bearer_scheme(request)
    settings = get_settings()
    return await get_current_user(request, auth=auth, settings=settings)
