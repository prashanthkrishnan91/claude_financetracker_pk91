"""Auth router — signup, login, profile management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..database import get_supabase_client
from ..middleware.auth import AuthenticatedUser, get_current_user
from ..models.user import UserApiKeysUpdate, UserCreate, UserResponse, UserUpdate
from ..services.crypto_service import encrypt_value

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def signup(user: UserCreate):
    """Create a new user via Supabase Auth and initialize their profile."""
    client = get_supabase_client()

    # Create auth user in Supabase
    try:
        auth_response = client.auth.sign_up({
            "email": user.email,
            "password": user.password,
        })
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Signup failed: {e}")

    if not auth_response.user:
        raise HTTPException(status_code=400, detail="Signup failed — no user returned")

    user_id = auth_response.user.id

    # Create extended profile in public.users
    profile = {
        "id": str(user_id),
        "email": user.email,
        "display_name": user.display_name,
        "deposit_amount": user.deposit_amount,
        "deposit_frequency": user.deposit_frequency,
        "theme": user.theme,
        "default_currency": user.default_currency,
    }

    result = client.table("users").insert(profile).execute()
    return result.data[0]


@router.post("/login")
async def login(email: str, password: str):
    """Login and return Supabase session tokens."""
    client = get_supabase_client()

    try:
        response = client.auth.sign_in_with_password({
            "email": email,
            "password": password,
        })
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Login failed: {e}")

    return {
        "access_token": response.session.access_token,
        "refresh_token": response.session.refresh_token,
        "expires_at": response.session.expires_at,
        "user": {
            "id": str(response.user.id),
            "email": response.user.email,
        },
    }


@router.get("/me", response_model=UserResponse)
async def get_profile(user: AuthenticatedUser = Depends(get_current_user)):
    """Get the current user's profile."""
    client = get_supabase_client()
    result = client.table("users").select("*").eq("id", str(user.id)).single().execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="User profile not found")

    # Add flags for which API keys are configured
    data = result.data
    data["has_plaid"] = bool(data.get("encrypted_plaid_access_token"))
    data["has_finnhub"] = bool(data.get("encrypted_finnhub_api_key"))
    data["has_polygon"] = bool(data.get("encrypted_polygon_api_key"))
    data["has_alpaca"] = bool(data.get("encrypted_alpaca_api_key"))

    return data


@router.patch("/me", response_model=UserResponse)
async def update_profile(
    updates: UserUpdate,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Update the current user's profile."""
    client = get_supabase_client()

    update_data = updates.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = (
        client.table("users")
        .update(update_data)
        .eq("id", str(user.id))
        .execute()
    )
    return result.data[0]


@router.put("/me/api-keys")
async def update_api_keys(
    keys: UserApiKeysUpdate,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Update encrypted API keys for the current user.

    Keys are encrypted with AES-256-GCM before storage.
    Only non-null fields are updated.
    """
    client = get_supabase_client()

    update_data = {}
    field_map = {
        "plaid_access_token": "encrypted_plaid_access_token",
        "plaid_client_id": "encrypted_plaid_client_id",
        "plaid_secret": "encrypted_plaid_secret",
        "plaid_env": "plaid_env",
        "finnhub_api_key": "encrypted_finnhub_api_key",
        "polygon_api_key": "encrypted_polygon_api_key",
        "alpaca_api_key": "encrypted_alpaca_api_key",
        "alpaca_secret_key": "encrypted_alpaca_secret_key",
    }

    for input_field, db_field in field_map.items():
        value = getattr(keys, input_field, None)
        if value is not None:
            # Encrypt secrets, pass through non-secret fields
            if "encrypted_" in db_field:
                update_data[db_field] = encrypt_value(value)
            else:
                update_data[db_field] = value

    if not update_data:
        raise HTTPException(status_code=400, detail="No keys to update")

    client.table("users").update(update_data).eq("id", str(user.id)).execute()
    return {"status": "ok", "keys_updated": list(update_data.keys())}
