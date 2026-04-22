"""Test configuration and fixtures."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def set_test_env(monkeypatch):
    """Set required environment variables for testing."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test-anon-key")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-jwt-secret-at-least-32-chars-long")
    monkeypatch.setenv("ENCRYPTION_KEY", "a" * 64)  # 32-byte hex
    monkeypatch.setenv("DEBUG", "true")
    # Disable the distributed-lock and provider-health sinks in tests —
    # otherwise the module singletons accumulate resolved-lock rows and
    # breaker state across tests, which leaks failures / stale results
    # into later tests.
    monkeypatch.setenv("DISTRIBUTED_LOCK_BACKEND", "off")
    monkeypatch.setenv("PROVIDER_HEALTH_STORE", "off")
    monkeypatch.setenv("SYSTEM_HEALTH_SINK", "off")


@pytest.fixture(autouse=True)
def _reset_distributed_singletons():
    """Reset module singletons between tests so state never leaks.

    The distributed lock + system-mode manager + provider breakers are all
    module-level singletons. Without this fixture a test that trips a
    breaker or publishes a lock result would poison every subsequent test
    that happens to use the same key.
    """
    try:
        from app.services.market_data import distributed_lock as dl
        from app.services.market_data import system_mode as sm
        from app.services.agents import data_sources as ds

        dl._set_lock_for_testing(None)
        sm._set_manager_for_testing(None)
        ds.reset_breakers()
    except Exception:
        pass
    yield
    try:
        from app.services.market_data import distributed_lock as dl
        from app.services.market_data import system_mode as sm
        from app.services.agents import data_sources as ds

        dl._set_lock_for_testing(None)
        sm._set_manager_for_testing(None)
        ds.reset_breakers()
    except Exception:
        pass
