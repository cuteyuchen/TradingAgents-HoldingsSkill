# V3-CORE-4 Analysis Run Artifact Export

## Status

- `IMPLEMENTED = YES`: Standard and Debug AnalysisRun evidence packages are built from persisted audit records.
- `TESTED = AUTOMATED_ONLY`: package, verification, redaction, and authorization coverage is automated.
- `MERGED = NO`: this branch is not an authorization to merge.
- `VERIFIED = NO`: production-scale exports, live providers, and manual UI behavior remain unverified.

## Scope

CORE-4 packages an existing `AnalysisRun` into an on-demand ZIP evidence package.
It does not create a second audit system. The authoritative records remain:

```text
AnalysisRun -> AnalysisStage -> AnalysisNode -> AnalysisNodeAttempt
            -> AnalysisArtifact
            -> AnalysisClaim
            -> Timeline / Checkpoint
```

The exporter reads those persisted rows in batches, assembles a package in a
short-lived staging directory, and streams the resulting ZIP. It never calls
`collect_market_snapshot()`, `refresh_snapshot_quotes()`, a provider, a model,
Resume, or the analysis executor. It does not write to the AnalysisRun or its
audit records.

No database migration is required. Evidence IDs and source IDs are built at
export time so old Runs remain readable without rewriting Claims.

## API

```text
GET /api/v2/analysis/runs/{run_id}/export?mode=standard
GET /api/v2/analysis/runs/{run_id}/export?mode=debug
```

`mode` must be `standard` or `debug`. The route loads the Run through the
existing user-scoped `_get_run()` helper, so guessing a Run ID cannot cross user
boundaries. Terminal `completed`, `blocked`, `failed`, `cancelled`, and
`interrupted` Runs are supported. Older historical Runs with missing audit
material are exported best-effort with explicit `not_available` markers.

The response is `application/zip` with an attachment filename:

```text
analysis-run-YYYYMMDD-HHMM-<run_id>-standard.zip
analysis-run-YYYYMMDD-HHMM-<run_id>-debug.zip
```

The timestamp prefers the persisted market/analysis date and falls back to the
Run creation time. ZIPs are generated only on request, sent using `FileResponse`,
and deleted with a response background task. They are not permanently stored.

## Standard Package

Every Standard package has root `manifest.json` and `README.md`, then includes
the following server-owned paths:

```text
run/
  analysis-run.json
  workflow.json
  configuration.json
  timeline.json

portfolio/
  portfolio-snapshot.json
  holdings.json

market/
  market-snapshot.json
  final-quote-refresh.json

evidence/
  evidence-index.json
  sources.json
  quality-gate.json

agents/
  summary.json

debate/
  investment-claims.json
  risk-claims.json
  claim-resolution.json          # only when persisted

decision/
  research-manager.json
  trader.json
  risk-manager.json
  risk-synthesis.json
  portfolio-manager.json
  portfolio-decision-gate.json
  candidates.json
  final-decision.json

errors/
  failures.json
  retries.json

reports/
  summary.md
  full-analysis.md
```

`run/analysis-run.json` separates `system_decision` from
`actual_execution`. If execution was not persisted, the latter is explicitly
`not_available`; the exporter never invents a trade execution.

`candidate_actions: []` and `NO_ACTION` are normal valid outcomes.

## Debug Additions

Debug contains all Standard files and adds only persisted diagnostic material:

```text
evidence/
  evidence-snapshot.json
  raw-artifacts-index.json

agents/<node_key>/
  input.json
  prompt-template.json
  rendered-prompt.json
  structured-output.json
  raw-output.txt
  attempts.json
  metadata.json

prompts/
  manifest.json

checkpoints/
  checkpoints.json

workflow/
  node-attempts.json
  stage-records.json

artifacts/
  index.json

errors/
  raw-errors.json
```

`attempts.json` records the durable attempt contract: attempt number, status,
start/end, latency, provider/model/request identity, input/output hashes,
token counts, transport and structured retries, failure class/type/code/message,
and retryability. Missing provider metadata remains null; it is not fabricated.

Old Runs that do not have a complete artifact history still return a Debug
package. `artifacts/index.json` marks `partial_debug=true` when the immutable
evidence snapshot or artifact history is absent.

## Manifest And Verification

The root `manifest.json` contains the package version, Run/Job identity,
trading date, checkpoint, mode, workflow/skill versions, Run status, data
quality, lifecycle timestamps, export mode/time, and one entry for every
non-manifest file:

```json
{
  "package_version": "v1",
  "run_id": 123,
  "job_id": 456,
  "trading_date": "2026-09-07",
  "export_mode": "standard",
  "files": [
    {
      "path": "decision/final-decision.json",
      "sha256": "...",
      "size": 1234,
      "required": true
    }
  ]
}
```

SHA-256 and byte size are calculated from the final redacted package bytes.
`manifest.json` is intentionally excluded from `files` to avoid recursive
hashing. `verify_export_package()` supports either a ZIP or an extracted package
directory. It rejects unsafe paths, duplicate ZIP members, missing files,
unlisted files, size mismatches, and SHA-256 mismatches.

## Evidence And Sources

CORE-3 frozen input paths are preserved. At export time they receive stable
package-local IDs such as `EV-000001`. `evidence/evidence-index.json` contains:

```json
{
  "evidence_id": "EV-000001",
  "path": "input.market.quotes.600519.price",
  "category": "market",
  "instrument": "600519",
  "observed_at": "...",
  "fetched_at": "...",
  "source_id": "SRC-001",
  "quality": "A",
  "provider_derived": true,
  "value_summary": "..."
}
```

`sources.json` uses `SRC-001` style identifiers and exports only safe provider
metadata: provider name, source type, observed/fetched times, quality, and
fallback status. Unavailable historic fields are marked `not_available`.

Historical Claims keep their original `evidence_refs`; the exported claim files
add `evidence_ids`, while the evidence index records `legacy_ref_to_evidence_id`.
No historic Claim is rewritten.

## Redaction And Hidden Reasoning

Every package value crosses the central redaction boundary again before writing.
This applies even when an Artifact was already marked redacted. Secret-shaped
fields and text patterns including API keys, authorization headers, Bearer
tokens, cookies, passwords, secrets, refresh tokens, and webhooks are removed.

The exporter drops fields such as `reasoning`, `reasoning_content`,
`chain_of_thought`, `thinking`, and `scratchpad`. JSON-like visible raw model
output is parsed and sanitized before being written; public conclusions,
claims, rationale summaries, and visible raw output remain exportable. Hidden
provider reasoning never enters the package.

## Limits And Safety

```text
ANALYSIS_EXPORT_MAX_BYTES=52428800
ANALYSIS_EXPORT_TEMP_DIR=
```

The byte limit guards both staged content and the resulting ZIP, returning a
clear error rather than returning a partial package or loading a complete ZIP
into memory. Internal archive paths are generated by the server and validated;
user input never forms a ZIP path, preventing Zip Slip.

## Automated Coverage

The CORE-4 test suite covers a Deep completed Run in both modes, prompts,
attempts, public raw output, checkpoints, artifact indexes, claims, final
decision, `candidate_actions: []`, failure/blocked/cancelled/interrupted
exports, user isolation, no re-analysis/live refresh, byte limits, full-package
secret scanning, hidden-reasoning sentinel removal, and verification failure
after modifying an extracted package file.

## Not Verified

- Real intraday market data and long-running provider stability.
- Production large Debug export volume and temporary-storage pressure.
- Manual browser UX and mobile behavior.
- Real broker execution, automatic trading, and investment returns.
