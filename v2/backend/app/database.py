"""Supabase client setup — singleton pattern for connection reuse."""

from __future__ import annotations

from functools import lru_cache

from supabase import Client, create_client

from .config import get_settings


@lru_cache
def get_supabase_client() -> Client:
    """Return a cached Supabase client using the service role key.

    The service role key bypasses RLS — use only in backend services
    where the user_id is explicitly passed from the auth middleware.
    For direct frontend queries, use the anon key.
    """
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


@lru_cache
def get_supabase_anon_client() -> Client:
    """Return a cached Supabase client using the anon key (RLS enforced)."""
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_anon_key)


def get_user_client(access_token: str) -> Client:
    """Return a Supabase client authenticated as a specific user.

    This client respects RLS policies — the user can only access their own data.
    Used when we want Supabase-level enforcement of data isolation.
    """
    settings = get_settings()
    client = create_client(settings.supabase_url, settings.supabase_anon_key)
    client.auth.set_session(access_token, "")
    return client
