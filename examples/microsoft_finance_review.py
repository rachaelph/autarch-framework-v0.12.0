"""Operate the persistent Microsoft Finance Intelligence release-review queue.

Reviewer names passed here are attribution only.  A deployed service must obtain
them from an authenticated identity provider and invoke the same store API with
``identity_provider`` set to that provider.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from autarch.review import ReleaseReviewStore


DEFAULT_DB = Path("./sandbox/microsoft_finance_intelligence/reviews/release_reviews.db")


def _summary(review: Any) -> Dict[str, Any]:
    value = review.as_dict()
    return {
        "id": value["id"],
        "release_key": value["release_key"],
        "subject": value["subject"],
        "status": value["status"],
        "artifact_digest": value["artifact_digest"],
        "required_reviewers": value["required_reviewers"],
        "approvals": review.approvals,
        "rejections": review.rejections,
        "votes_remaining": value["votes_remaining"],
        "created_at": value["created_at"],
        "decided_at": value["decided_at"],
        "expires_at": value["expires_at"],
        "superseded_by": value["superseded_by"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Operate digest-bound Microsoft Finance Intelligence reviews"
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="review SQLite database")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="list review requests")
    list_parser.add_argument("--status", help="optional status filter")

    status_parser = sub.add_parser("status", help="show one review")
    status_parser.add_argument("review_id")

    for command in ("approve", "reject", "comment"):
        action = sub.add_parser(command, help=f"{command} a review")
        action.add_argument("review_id")
        action.add_argument("--reviewer", required=True, help="authenticated principal in deployed use")
        action.add_argument("--comment", required=(command in {"reject", "comment"}))
        action.add_argument("--identity-provider", default="operator-cli-asserted")

    supersede = sub.add_parser("supersede", help="administratively supersede a review")
    supersede.add_argument("review_id")
    supersede.add_argument("--by", required=True)
    supersede.add_argument("--reason", required=True)

    sub.add_parser("verify", help="verify the review-event hash chain")
    return parser


def main() -> None:
    args = _parser().parse_args()
    store = ReleaseReviewStore(args.db)
    if args.command == "list":
        output = [_summary(review) for review in store.list(status=args.status)]
    elif args.command == "status":
        review = store.get(args.review_id)
        if review is None:
            raise SystemExit(f"No review '{args.review_id}'")
        output = review.as_dict()
    elif args.command == "approve":
        output = store.approve(
            args.review_id,
            args.reviewer,
            comment=args.comment or "",
            identity_provider=args.identity_provider,
        ).as_dict()
    elif args.command == "reject":
        output = store.reject(
            args.review_id,
            args.reviewer,
            comment=args.comment,
            identity_provider=args.identity_provider,
        ).as_dict()
    elif args.command == "comment":
        output = store.add_comment(
            args.review_id,
            args.reviewer,
            args.comment,
            identity_provider=args.identity_provider,
        ).as_dict()
    elif args.command == "supersede":
        output = store.supersede(
            args.review_id, by=args.by, reason=args.reason
        ).as_dict()
    else:
        verified, broken = store.verify_chain()
        output = {"verified": verified, "broken_sequence": broken}
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()