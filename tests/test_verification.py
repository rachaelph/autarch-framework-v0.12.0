"""Tests for exhaustive kernel invariant verification."""
from autarch.verification import verify_kernel


def test_kernel_invariants_hold():
    result = verify_kernel()
    assert result.holds, result.summary()
    assert result.checked > 1000  # a meaningful bounded space, not a token check
    assert len(result.invariants) == 4


def test_result_summary_readable():
    result = verify_kernel(max_cases=200)
    assert "kernel invariants" in result.summary()
