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
