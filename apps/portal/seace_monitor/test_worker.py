"""Tests del worker periódico."""

from .worker import seconds_until_next_wake


def test_seconds_until_next_wake_prefers_soonest_job():
    now = 1000.0
    assert seconds_until_next_wake(now, now + 3600, now + 1800) == 1800.0


def test_seconds_until_next_wake_minimum_one_second():
    now = 1000.0
    assert seconds_until_next_wake(now, now + 0.2, now + 0.1) == 1.0
