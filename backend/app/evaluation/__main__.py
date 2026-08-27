"""Bounded command-line entry point for Phase I evidence operations."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime

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
from .forward import (
    campaign_coverage,
    campaign_integrity,
    create_daily_evidence_seal,
    create_observation_campaign,
    forward_summary,
    get_observation_campaign,
    list_observation_campaigns,
    mature_campaign_outcomes,
    transition_campaign,
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
    sub.add_parser("campaign-list")
    campaign_create = sub.add_parser("campaign-create")
    campaign_create.add_argument("--start", type=date.fromisoformat)
    campaign_create.add_argument("--end", type=date.fromisoformat)
    campaign_create.add_argument("--config-hash")
    campaign_status = sub.add_parser("campaign-status")
    campaign_status.add_argument("campaign_id")
    for name in ("campaign-start", "campaign-pause", "campaign-resume", "campaign-complete", "campaign-coverage", "campaign-integrity", "forward-summary"):
        command_parser = sub.add_parser(name)
        if name != "campaign-list":
            command_parser.add_argument("campaign_id")
    seal = sub.add_parser("daily-seal")
    seal.add_argument("campaign_id")
    seal.add_argument("trading_date", type=date.fromisoformat)
    mature = sub.add_parser("mature-outcomes")
    mature.add_argument("campaign_id")
    mature.add_argument("--as-of", type=datetime.fromisoformat)
    mature.add_argument("--limit", type=int, default=1000)
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
        elif args.command == "campaign-list":
            result = list_observation_campaigns(db, user_id=args.user_id, portfolio_id=args.portfolio_id)
        elif args.command == "campaign-create":
            result = create_observation_campaign(
                db,
                user_id=args.user_id,
                portfolio_id=args.portfolio_id,
                start_date=args.start,
                end_date=args.end,
                config_hash=args.config_hash,
            )
        elif args.command == "campaign-status":
            result = get_observation_campaign(db, campaign_id=args.campaign_id, user_id=args.user_id, portfolio_id=args.portfolio_id)
        elif args.command in {"campaign-start", "campaign-pause", "campaign-resume", "campaign-complete"}:
            result = transition_campaign(
                db,
                campaign_id=args.campaign_id,
                user_id=args.user_id,
                portfolio_id=args.portfolio_id,
                action=args.command.removeprefix("campaign-"),
            )
        elif args.command == "campaign-coverage":
            result = campaign_coverage(db, campaign_id=args.campaign_id, user_id=args.user_id, portfolio_id=args.portfolio_id)
        elif args.command == "campaign-integrity":
            result = campaign_integrity(db, campaign_id=args.campaign_id, user_id=args.user_id, portfolio_id=args.portfolio_id)
        elif args.command == "forward-summary":
            result = forward_summary(db, campaign_id=args.campaign_id, user_id=args.user_id, portfolio_id=args.portfolio_id)
        elif args.command == "daily-seal":
            result = create_daily_evidence_seal(
                db,
                campaign_id=args.campaign_id,
                user_id=args.user_id,
                portfolio_id=args.portfolio_id,
                trading_date=args.trading_date,
            )
        elif args.command == "mature-outcomes":
            result = mature_campaign_outcomes(
                db,
                campaign_id=args.campaign_id,
                user_id=args.user_id,
                portfolio_id=args.portfolio_id,
                as_of=args.as_of,
                limit=args.limit,
            )
        else:
            result = paper_observation_status(db, user_id=args.user_id, portfolio_id=args.portfolio_id)
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
