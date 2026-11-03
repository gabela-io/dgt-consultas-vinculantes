"""Unit tests for the AIMD TokenBucket."""

from dgt_consultas.ratelimit import TokenBucket


def test_initial_rate_clamped_to_bounds():
    b = TokenBucket(rate=100.0, min_rate=0.5, max_rate=2.0)
    assert b.rate == 2.0

    b2 = TokenBucket(rate=0.01, min_rate=0.5, max_rate=2.0)
    assert b2.rate == 0.5


def test_record_failure_halves_rate_floored_at_min():
    b = TokenBucket(rate=2.0, min_rate=0.5, max_rate=4.0)
    b.record_failure()
    assert b.rate == 1.0
    b.record_failure()
    assert b.rate == 0.5
    b.record_failure()  # already at floor
    assert b.rate == 0.5


def test_record_success_increases_after_threshold():
    b = TokenBucket(
        rate=1.0, min_rate=0.2, max_rate=4.0, aimd_step=0.1, success_threshold=3
    )
    for _ in range(2):
        b.record_success()
    assert b.rate == 1.0  # not yet
    b.record_success()
    assert b.rate == 1.1
    # streak resets; needs 3 more to bump again
    for _ in range(2):
        b.record_success()
    assert b.rate == 1.1
    b.record_success()
    assert b.rate == 1.2000000000000002 or abs(b.rate - 1.2) < 1e-9


def test_record_success_capped_at_max():
    b = TokenBucket(
        rate=3.95, min_rate=0.1, max_rate=4.0, aimd_step=0.1, success_threshold=1
    )
    b.record_success()
    assert b.rate == 4.0
    b.record_success()
    assert b.rate == 4.0


def test_failure_resets_success_streak():
    b = TokenBucket(
        rate=1.0, min_rate=0.2, max_rate=4.0, aimd_step=0.1, success_threshold=3
    )
    b.record_success()
    b.record_success()
    b.record_failure()
    assert b.rate == 0.5
    # streak was reset; need full threshold again to bump
    for _ in range(2):
        b.record_success()
    assert b.rate == 0.5
    b.record_success()
    assert b.rate == 0.6000000000000001 or abs(b.rate - 0.6) < 1e-9
