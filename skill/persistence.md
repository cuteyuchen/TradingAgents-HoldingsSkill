# Persistence System Integration

This file defines the contract between this skill and the companion persistence system (`ZCodeProject` backend). It implements Phase 0 (history fetch) and Phase 6 (run upload). The skill runs standalone when the system is not configured.

## Activation

Persistence is active when **both** environment variables are set:

| Env var | Example | Purpose |
|---|---|---|
| `ADVISOR_API_URL` | `http://localhost:8000/api/v1` | Backend base URL (no trailing slash) |
| `ADVISOR_TOKEN` | `adv_aBcD...` | Bearer token for write/read auth |

If either is unset, the skill runs without persistence (trading memory falls back to conversation history, no upload). Never block advice on persistence being unavailable.

## Phase 0 — History Fetch (start of run)

Before analysis, fetch prior decisions for in-scope holdings to seed trading memory.

**Preferred request:** `GET {ADVISOR_API_URL}/memory/context?code={code}&same_limit=5&cross_limit=3`

**Backward-compatible request:** `GET {ADVISOR_API_URL}/holdings/{code}/timeline?limit=5` (same-ticker timeline only)

**Response:**
```json
{
  "code": "600519",
  "same_ticker": [
    {"run_id": 12, "timestamp": "2026-06-17T10:00:00", "checkpoint": "10:00",
     "price": 1680.0, "raw_return": null, "benchmark_return": null, "alpha": null,
     "pm_rating": "Hold", "action": "Hold"}
  ],
  "cross_ticker_lessons": [
    {"run_id": 21, "code": "000001", "alpha": -0.02,
     "lesson": "跨标的经验：强势市场先减弱势仓"}
  ]
}
```

**Usage:** Inject the last `memory_same_ticker_entries` (default 5) same-ticker points + `memory_cross_ticker_lessons` (default 3) cross-ticker lessons into the Portfolio Manager's context. If `alpha` is present and negative, apply `negative_alpha_sizing` (reduce confidence, tighten sizing). See `trading-rules.md` Trading Memory.

## Phase 6 — Run Upload (end of run)

After producing advice, upload the full run so it is queryable in the dashboard.

**Request:** `POST {ADVISOR_API_URL}/runs` with header `Authorization: Bearer {ADVISOR_TOKEN}`

The body is the skill's output contract (see `python-execution.md`) plus the 8-section transcript. All sections are optional — a degraded run uploads what it has:

```json
{
  "timestamp": "2026-06-18T10:00:00",
  "checkpoint": "10:00",
  "holdings_source": "screenshot",
  "data_quality_grade": "B",
  "intent": {"tickers": ["600519"], "horizon": "short", "focus": ["技术"], "risk_profile": "稳健"},
  "evidence_pack": {"code_assumptions": {"600519": "high"}, "missing_fields": []},
  "transcript": "完整8段原文 transcript",
  "sections": {
    "evidence": "证据包",
    "quality_gate": "质量门控",
    "investment_debate": "多空辩论",
    "research_verdict": "研究总监裁决",
    "trader_proposal": "交易员方案",
    "risk_debate": "三方风控辩论",
    "pm_final": "组合经理结论",
    "candidates": "候选表"
  },
  "quality_gates": [
    {"analyst": "技术分析", "hard_check": "pass", "llm_review": "通过", "grade": "A", "gaps": null}
  ],
  "holdings": [
    {"code": "600519", "name": "贵州茅台", "qty": 100, "cost": 1700, "price": 1680,
     "data_quality": "B",
     "indicators": {
       "quote": {"price": 1680, "pct_change": -1.2, "volume_ratio": 1.3},
       "technicals": {"rsi_14": 45.2, "macd_signal": "below_zero", "ma_5": 1690, "ma_20": 1710},
       "vpa": {"obv_trend": "down", "bearish_divergence": false},
       "fund_flow": {"super_large_net": -2.1e8},
       "red_flags": []
     }}
  ],
  "claims": [
    {"claim_id": "INV-1", "speaker": "bull", "stance": "bullish", "claim": "政策支持",
     "evidence": ["证据1"], "confidence": 0.8, "status": "open", "round": 1}
  ],
  "research_verdict": {"rating": "Hold", "winner": "bull", "rationale": "...", "confidence": "中"},
  "trader_proposals": [
    {"code": "600519", "action": "Hold", "trigger_price": null, "qty": null,
     "stop_loss": "1650", "invalidation": "跌破1650",
     "revision": {"verdict": "pass"}}
  ],
  "pm_final": {"rating": "Hold", "cash_target": "20%", "priority_notes": "..."},
  "candidates": [
    {"code": "512480", "name": "半导体ETF", "type": "ETF", "score": 7.5,
     "entry_trigger": "突破1.050", "initial_size": "10%", "stop_loss": "1.020",
     "invalidation": "板块跌超2%", "status": "待触发"}
  ]
}
```

**Response:**
```json
{"run_id": 42, "alphas": {"600519": {"raw_return": 0.02, "benchmark_return": 0.01, "alpha": 0.01}}}
```

The backend computes alpha by comparing this run's holding price to the previous same-code run, minus CSI 300 over the same window. The skill does **not** compute alpha itself when persistence is configured — it reads the returned `alphas` or fetches it next run via Phase 0.

## Upload via curl

```bash
curl -X POST "${ADVISOR_API_URL}/runs" \
  -H "Authorization: Bearer ${ADVISOR_TOKEN}" \
  -H "Content-Type: application/json" \
  -d @/tmp/run_payload.json
```

## Upload via Python (recommended for skill)

```python
import os, json, requests

def upload_run(payload: dict) -> dict | None:
    url = os.getenv("ADVISOR_API_URL")
    token = os.getenv("ADVISOR_TOKEN")
    if not url or not token:
        return None  # persistence not configured — run without it
    try:
        r = requests.post(
            f"{url}/runs",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        # Do not block advice; surface to user.
        print(f"[未持久化: {exc}]")
        return None
```

## Failure Policy (non-negotiable)

- Upload failure must **not** block the advice to the user. Finish the advice, then attempt upload.
- On failure, append a note to the advice: `[未持久化: {reason}]`.
- On success, no extra note is needed (the dashboard is the source of truth).
- Never retry more than once in a single run.

## Health Reporting (optional)

If data fetching struggled, report the outcome so the dashboard can flag degraded checkpoints:

```bash
curl -X POST "${ADVISOR_API_URL}/health/outcome" \
  -H "Authorization: Bearer ${ADVISOR_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"code": "600519", "checkpoint": "10:00", "success": false, "note": "东财封禁"}'
```

After `consecutive_failure_threshold` (default 3) failures for the same code + checkpoint, the dashboard marks it degraded and disables the matching watchlist item. A later success clears the failure counter but does not automatically re-enable the watchlist item.
