from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.triggers.plans import extract_trigger_plans_from_analysis_run
from app.market_models import SecurityMaster


def _db():
    engine = create_engine("sqlite:///:memory:")
    SecurityMaster.__table__.create(engine)
    return Session(engine)


def test_extracts_only_explicit_structured_condition():
    db_session = _db()
    db_session.add(SecurityMaster(market="CN", exchange="SSE", code="600519", name="Moutai", security_type="STOCK", status="ACTIVE"))
    db_session.flush()
    run = SimpleNamespace(
        id=7,
        user_id=1,
        portfolio_snapshot_id=None,
        structured_result_json={"result": {"holdings": [{"code": "600519", "trigger": {"condition": "price_below", "threshold": 100}}]}},
        job=SimpleNamespace(portfolio_id=2),
    )
    plans = extract_trigger_plans_from_analysis_run(db_session, run)
    assert len(plans) == 1
    assert plans[0].operator == "LT"
    assert plans[0].threshold == 100
    db_session.close()


def test_skips_natural_language_trigger():
    db_session = _db()
    db_session.add(SecurityMaster(market="CN", exchange="SSE", code="600519", name="Moutai", security_type="STOCK", status="ACTIVE"))
    db_session.flush()
    run = SimpleNamespace(id=8, user_id=1, portfolio_snapshot_id=None,
                          structured_result_json={"result": {"holdings": [{"code": "600519", "trigger": "跌破均线"}]}},
                          job=SimpleNamespace(portfolio_id=2))
    assert extract_trigger_plans_from_analysis_run(db_session, run) == []
    db_session.close()
