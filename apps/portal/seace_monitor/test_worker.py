"""Tests del worker periódico."""

from .worker import seconds_until_next_wake


def test_seconds_until_next_wake_prefers_soonest_job():
    now = 1000.0
    assert seconds_until_next_wake(now, now + 3600, now + 1800) == 1800.0


def test_seconds_until_next_wake_minimum_one_second():
    now = 1000.0
    assert seconds_until_next_wake(now, now + 0.2, now + 0.1) == 1.0


def test_seconds_until_next_wake_uses_post_work_clock():
    loop_start = 1000.0
    after_work = loop_start + 30.0
    next_scan_at = loop_start + 60.0
    assert seconds_until_next_wake(after_work, next_scan_at, loop_start + 180.0) == 30.0
