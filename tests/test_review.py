"""Digest-bound release review lifecycle tests."""
from __future__ import annotations

import time

import pytest

from autarch.review import (
    APPROVED,
    EXPIRED,
    PENDING,
    REJECTED,
    SUPERSEDED,
    ReleaseReviewStore,
    canonical_sha256,
)


def _store(tmp_path):
    return ReleaseReviewStore(tmp_path / "reviews.db")


def test_named_quorum_is_idempotent_and_digest_bound(tmp_path) -> None:
    store = _store(tmp_path)
    digest = canonical_sha256({"release": 1})
    review = store.submit(
        "MSFT:FY2026",
        "Microsoft FY2026 release",
        digest,
        ["finance-lead", "risk-lead"],
        quorum=2,
    )

    assert review.status == PENDING
    assert store.submit(
        "MSFT:FY2026", "Microsoft FY2026 release", digest,
        ["finance-lead", "risk-lead"], quorum=2,
    ).id == review.id
    assert store.approve(review.id, "finance-lead", comment="financials checked").status == PENDING
    approved = store.approve(review.id, "risk-lead", comment="risk text checked")

    assert approved.status == APPROVED
    assert approved.valid_for(digest)
    assert not approved.valid_for(canonical_sha256({"release": 2}))
    assert store.require_valid(review.id, digest).id == review.id


def test_changed_content_supersedes_prior_approval(tmp_path) -> None:
    store = _store(tmp_path)
    first_digest = canonical_sha256({"version": 1})
    first = store.submit("release", "Release", first_digest, ["owner"])
    store.approve(first.id, "owner")

    second = store.submit(
        "release", "Release", canonical_sha256({"version": 2}), ["owner"]
    )

    assert second.id != first.id and second.status == PENDING
    assert store.get(first.id).status == SUPERSEDED
    assert store.get(first.id).superseded_by == second.id


def test_changed_review_contract_cannot_reuse_prior_request(tmp_path) -> None:
    store = _store(tmp_path)
    digest = canonical_sha256({"version": 1})
    first = store.submit(
        "release", "Release", digest, ["finance-lead"],
        requested_by="workflow", rationale="single review",
    )

    second = store.submit(
        "release", "Release", digest, ["finance-lead", "risk-lead"],
        quorum=2, requested_by="workflow", rationale="two-person review",
    )

    assert second.id != first.id
    assert second.status == PENDING
    assert second.required_reviewers == ["finance-lead", "risk-lead"]
    assert store.get(first.id).status == SUPERSEDED
    assert store.verify_chain() == (True, None)


def test_rejection_is_immediate_and_requires_reason(tmp_path) -> None:
    store = _store(tmp_path)
    review = store.submit(
        "release", "Release", canonical_sha256({"x": 1}), ["a", "b"], quorum=2
    )

    with pytest.raises(ValueError, match="rejection comment"):
        store.reject(review.id, "a", comment="")
    rejected = store.reject(review.id, "a", comment="unsupported assumption")

    assert rejected.status == REJECTED
    assert rejected.rejections == ["a"]


def test_unauthorized_reviewer_cannot_vote_or_comment(tmp_path) -> None:
    store = _store(tmp_path)
    review = store.submit(
        "release", "Release", canonical_sha256({"x": 1}), ["authorized"]
    )

    with pytest.raises(PermissionError, match="not authorized"):
        store.approve(review.id, "intruder")
    with pytest.raises(PermissionError, match="not authorized"):
        store.add_comment(review.id, "intruder", "looks fine")


def test_pending_review_expires_and_fails_closed(tmp_path) -> None:
    store = _store(tmp_path)
    review = store.submit(
        "release", "Release", canonical_sha256({"x": 1}), ["owner"], ttl_seconds=0.01
    )
    time.sleep(0.02)

    assert store.get(review.id).status == EXPIRED
    with pytest.raises(PermissionError):
        store.require_valid(review.id, review.artifact_digest)


def test_review_event_chain_verifies(tmp_path) -> None:
    store = _store(tmp_path)
    review = store.submit(
        "release", "Release", canonical_sha256({"x": 1}), ["owner"]
    )
    store.add_comment(review.id, "owner", "source tie-out complete")
    store.approve(review.id, "owner", comment="approved")

    assert store.verify_chain() == (True, None)
    assert [event["event_type"] for event in store.events(review.id)] == [
        "submitted", "comment_added", "vote_recorded", "review_decided"
    ]