"""Tests for auth middleware — AuthenticatedUser model."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.middleware.auth import AuthenticatedUser


class TestAuthenticatedUser:
    def test_basic_construction(self):
        uid = uuid4()
        user = AuthenticatedUser(user_id=uid, email="test@example.com")
        assert user.id == uid
        assert user.email == "test@example.com"
        assert user.role == "owner"

    def test_family_role(self):
        user = AuthenticatedUser(user_id=uuid4(), email="wife@example.com", role="family")
        assert user.role == "family"

    def test_uuid_type(self):
        uid = uuid4()
        user = AuthenticatedUser(user_id=uid, email="test@example.com")
        assert isinstance(user.id, UUID)
