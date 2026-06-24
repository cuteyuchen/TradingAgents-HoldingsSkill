# Persistence System Integration

This file defines the current archive-only contract between this skill and the
companion backend/frontend. It intentionally documents only endpoints the skill
is allowed to use. Do not call non-archive backend endpoints from the skill.

## Activation

Persistence is active when **both** environment variables are set:

| Env var | Example | Purpose |
|---|---|---|
| `ADVISOR_API_URL` | `https://trade.cuteyuchen.top/api/v1` | Backend API base URL, no trailing slash |
| `ADVISOR_TOKEN` | `adv_aBcD...` | Bearer token for archive read/write auth |

If either is unset, finish the advice normally and skip archive upload. Never
block advice on persistence being unavailable.

## Active Endpoint Surface

The skill may use only the archive endpoints below:

| Method | Endpoint | Skill usage |
|---|---|---|
| `POST` | `{ADVISOR_API_URL}/archives` | Upload the already-visible advice Markdown, normalized holdings JSON, and original screenshot |
| `GET` | `{ADVISOR_API_URL}/archives` | Optional manual verification or user-requested archive list |
| `GET` | `{ADVISOR_API_URL}/archives/{id}` | Optional manual verification or user-requested archive detail |

`DELETE /archives/{id}` exists for the frontend archive manager, but the skill
must not delete archives unless the user explicitly asks.

Do **not** use non-archive backend endpoints in skill execution. They may exist
for the frontend or legacy structured views, but they are outside this skill's
active integration contract.

## Archive Upload

First display the final advice to the user. Only after the advice is visible,
upload an archive so the dashboard can render the same Markdown, parsed
holdings, and original screenshot later. Upload is archival only: it must not
participate in advice generation and must not change the already displayed
recommendation.

**Request:** `POST {ADVISOR_API_URL}/archives` with header
`Authorization: Bearer {ADVISOR_TOKEN}` and multipart form fields:

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

For account funds, when `total_assets` and `total_market_value` are visible in
the screenshot, persist corrected unused funds as
`total_assets - total_market_value`. Keep the broker "available cash" value only
as `broker_available_cash`, because pending-order funds may not have returned to
availability yet. Do not persist "新标准券", standard bond, treasury reverse repo,
or 国债逆回购 rows inside `holdings[]`; persist them under account fund fields
such as `repo_or_standard_bond_value` or under `excluded_items[]` with a reason.

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

## Upload via Python

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
        return None
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
        print(f"[未持久化: {exc}]")
        return None
```

## Failure Policy

- Upload failure must **not** block the advice to the user. Finish the advice,
  then attempt upload.
- On failure, append a note to the advice: `[未持久化: {reason}]`.
- If any holding code still needs user confirmation after public matching, do
  not attempt upload; append `[未持久化: 待确认代码]` and ask the user to
  provide/choose the code.
- On success, no extra note is needed.
- Never retry more than once in a single run.
