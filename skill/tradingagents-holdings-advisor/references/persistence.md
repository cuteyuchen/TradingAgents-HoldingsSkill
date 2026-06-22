# Persistence System Integration

This file defines the contract between this skill and the companion persistence system (`ZCodeProject` backend). It implements Phase 0 (history fetch) and Phase 6 (post-advice archive upload). The skill runs standalone when the system is not configured.

## Activation

Persistence is active when **both** environment variables are set:

| Env var | Example | Purpose |
|---|---|---|
| `ADVISOR_API_URL` | `http://localhost:8000/api/v1` | Backend base URL (no trailing slash) |
| `ADVISOR_TOKEN` | `adv_aBcD...` | Bearer token for write/read auth |

If either is unset, the skill runs without persistence (trading memory falls back to conversation history, no archive upload). Never block advice on persistence being unavailable.

## Phase 0 — History Fetch (start of run)

Before analysis, fetch prior decisions for in-scope holdings to seed trading memory.

**Preferred request:** `GET {ADVISOR_API_URL}/memory/context?code={code}&same_limit=5&cross_limit=3` with `Authorization: Bearer {ADVISOR_TOKEN}`

**Backward-compatible request:** `GET {ADVISOR_API_URL}/holdings/{code}/timeline?limit=5` with `Authorization: Bearer {ADVISOR_TOKEN}` (same-ticker timeline only)

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

## Phase 6 — Archive Upload (after advice is visible)

First display the final advice to the user. Only after the advice is visible,
upload an archive so the dashboard can render the same Markdown, parsed
holdings, and original screenshot later. Upload is archival only: it must not
participate in advice generation and must not change the already displayed
recommendation.

**Request:** `POST {ADVISOR_API_URL}/archives` with header `Authorization: Bearer {ADVISOR_TOKEN}` and multipart form fields:

| Field | Required | Type | Notes |
|---|---:|---|---|
| `screenshot` | yes | file | Original holdings screenshot (`png/jpg/webp/gif`) |
| `holdings_json` | yes | file | UTF-8 JSON file containing parsed holdings and evidence fields |
| `advice_md` | yes | file | UTF-8 Markdown file containing the visible advice and reasoning process |
| `meta` | no | string JSON | `{timestamp, checkpoint, holdings_source, data_quality_grade, title}` |

For holdings, `qty` is total position and `available_qty` is only currently
sellable/usable quantity. `qty - available_qty` means unavailable because of
pending orders, freeze, or T+1 limits; it must not be inferred as already
reduced/sold. Any reduce/sell recommendation must be sized from
`available_qty`.

For P/L, `pnl` is always the decimal return ratio and `pnl_amount` is the
currency amount. In a normal 同花顺/券商 two-line 盈亏 cell, line 1 is amount and
line 2 is percent; persist them directly as `pnl_amount` and `pnl`.

**Response:**
```json
{"id": 42}
```

## Upload via curl

```bash
curl -X POST "${ADVISOR_API_URL}/archives" \
  -H "Authorization: Bearer ${ADVISOR_TOKEN}" \
  -F "meta={\"timestamp\":\"2026-06-18T10:00:00+08:00\",\"checkpoint\":\"10:00\",\"holdings_source\":\"screenshot\",\"data_quality_grade\":\"A\",\"title\":\"10:00 持仓分析\"}" \
  -F "screenshot=@/tmp/holdings.png;type=image/png" \
  -F "holdings_json=@/tmp/holdings.json;type=application/json" \
  -F "advice_md=@/tmp/advice.md;type=text/markdown"
```

## Upload via Python (recommended for skill)

```python
import json
import os
from pathlib import Path

import requests

def upload_archive(
    screenshot_path: str,
    holdings_json_path: str,
    advice_md_path: str,
    meta: dict | None = None,
) -> dict | None:
    url = os.getenv("ADVISOR_API_URL")
    token = os.getenv("ADVISOR_TOKEN")
    if not url or not token:
        return None  # persistence not configured; run without it
    try:
        with (
            open(screenshot_path, "rb") as screenshot_file,
            open(holdings_json_path, "rb") as holdings_file,
            open(advice_md_path, "rb") as advice_file,
        ):
            files = {
                "screenshot": (Path(screenshot_path).name, screenshot_file),
                "holdings_json": ("holdings.json", holdings_file, "application/json"),
                "advice_md": ("advice.md", advice_file, "text/markdown"),
            }
            data = {"meta": json.dumps(meta or {}, ensure_ascii=False)}
            r = requests.post(
                f"{url}/archives",
                headers={"Authorization": f"Bearer {token}"},
                files=files,
                data=data,
                timeout=20,
            )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        # Do not block or rewrite advice; surface to user after visible output.
        print(f"[未持久化: {exc}]")
        return None
```

## Failure Policy (non-negotiable)

- Upload failure must **not** block the advice to the user. Finish the advice, then attempt upload.
- On failure, append a note to the advice: `[未持久化: {reason}]`.
- If any holding code still needs user confirmation after public matching, do not attempt upload; append `[未持久化: 待确认代码]` and ask the user to provide/choose the code.
- On success, no extra note is needed (the dashboard is the source of truth).
- Never retry more than once in a single run.

## Health Reporting (optional)

If data fetching struggled, report the outcome so the dashboard can flag quality-warning checkpoints:

```bash
curl -X POST "${ADVISOR_API_URL}/health/outcome" \
  -H "Authorization: Bearer ${ADVISOR_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"code": "600519", "checkpoint": "10:00", "success": false, "note": "东财封禁"}'
```

After `consecutive_failure_threshold` (default 3) failures for the same code + checkpoint, the dashboard marks the checkpoint as blocked/quality-warning and disables the matching watchlist item. A later success clears the failure counter but does not automatically re-enable the watchlist item.
