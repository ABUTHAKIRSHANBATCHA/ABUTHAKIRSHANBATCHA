"""Shared test setup."""
import pytest

from assistant import subscriptions


@pytest.fixture(autouse=True)
def no_real_subscription_programs(monkeypatch):
    """Tests must never start the real `claude` or `codex` programs that may be installed on the
    machine running them. Only a program path given explicitly by a test is used."""
    real_find = subscriptions.find
    monkeypatch.setattr(subscriptions, "find", lambda name, override="": real_find(name, override) if override else "")
