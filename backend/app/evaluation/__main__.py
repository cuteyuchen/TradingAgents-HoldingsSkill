"""Bounded command-line entry point for Phase I evidence operations."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from sqlalchemy import select

from ..database import SessionLocal, init_db
from ..v2_models import Portfolio
from .service import (
    evaluation_coverage,
    evaluation_summary,
    observe_pending_outcomes,
    paper_observation_status,
    replay_date_range,
    replay_episode,
    verify_snapshot_hashes,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.evaluation")
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--portfolio-id", type=int, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    single = sub.add_parser("replay-episode")
    single.add_argument("episode_id")
    single.add_argument("--mode", default="FACT_REPLAY")
    ranged = sub.add_parser("replay-range")
    ranged.add_argument("start", type=date.fromisoformat)
    ranged.add_argument("end", type=date.fromisoformat)
    ranged.add_argument("--mode", default="FACT_REPLAY")
    pending = sub.add_parser("calculate-pending-outcomes")
    pending.add_argument("--limit", type=int, default=1000)
    sub.add_parser("coverage")
    sub.add_parser("summary")
    sub.add_parser("paper-observation-status")
    sub.add_parser("verify-snapshot-hashes")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    init_db()
    with SessionLocal() as db:
        portfolio = db.execute(
            select(Portfolio).where(Portfolio.id == args.portfolio_id, Portfolio.user_id == args.user_id)
        ).scalar_one_or_none()
        if portfolio is None:
            print(json.dumps({"error": "portfolio_not_found"}, ensure_ascii=False))
            return 2
        if args.command == "replay-episode":
            result = replay_episode(db, episode_id=args.episode_id, user_id=args.user_id, portfolio_id=args.portfolio_id, mode=args.mode)
        elif args.command == "replay-range":
            result = replay_date_range(db, user_id=args.user_id, portfolio_id=args.portfolio_id, start=args.start, end=args.end, mode=args.mode)
        elif args.command == "calculate-pending-outcomes":
            result = observe_pending_outcomes(db, user_id=args.user_id, portfolio_id=args.portfolio_id, limit=args.limit)
        elif args.command == "coverage":
            result = evaluation_coverage(db, user_id=args.user_id, portfolio_id=args.portfolio_id)
        elif args.command == "summary":
            result = evaluation_summary(db, user_id=args.user_id, portfolio_id=args.portfolio_id)
        elif args.command == "verify-snapshot-hashes":
            result = verify_snapshot_hashes(db, user_id=args.user_id, portfolio_id=args.portfolio_id)
        else:
            result = paper_observation_status(db, user_id=args.user_id, portfolio_id=args.portfolio_id)
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
