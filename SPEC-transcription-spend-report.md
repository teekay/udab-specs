# Transcription Spend & Usage Reporting — V1

Status: READY FOR DEV 2026-08-28. Branches: udab-server
`calc-deepgram-usage-costs`, udab-client `show-deepgram-usage-costs`.
Builds on SPEC-transcription-auto.md (shipped) and the
`deepgram_request_id` column (shipped 2026-08-27).

## Requirement (Anna, product, verbatim)

> Possible to see pricing alongside each run?
> -> Will need to eventually be able to aggregate some of this reporting
> to show: how much the cost is within the selected timeframe (total)
> -> how many completed runs within selected time frame (any other
> status, also)
> Example: If I select this month (MTD) as the timeframe, it would show
> me each row, but also show me/provide a way for me to see how much the
> total cost was, how many of each call result were processed (and of
> each status)

Bonus requirement (Tomas): downloadable CSV reports as the client's
semi-permanent artifact.

## Decisions (2026-08-28, Tomas)

- **Costs are computed, never stored.** No cost columns anywhere; the UI
  labels every figure "estimated".
- **Duration source: join to the `sf_task` mirror**, not a new column.
  `sp_transcription_job_task.sf_task_sf_id → sf_task.sf_id`, duration =
  `sf_task.CallDurationInSeconds`. Validated against Deepgram's billed
  duration on 21 PROD requests across both vendors (probe scripts
  `udab-server/scripts/check_deepgram_cost.py` /
  `check_duration_match.py`, 2026-08-27/28): CloudCall mean delta
  +0.3 s (−0.4..+0.9), Orum +0.5 s (0.0..+1.0) — pure integer-rounding
  noise, aggregate error ≈0.25% before bias correction. Free win: works
  for ALL historical rows, so no backfill and no forward-only caveat.
- **Rate: empirical, one documented constant.** Pooled over the 21
  samples: $0.55598 / 127.96 billed-min = **$0.004345/billed-minute**
  (billed minutes = audio minutes × 2 channels; ×2 verified — Deepgram
  bills each channel). ~1% above the published $0.0043 nova-3 list rate;
  the invoice is the authority, so we ship the measured value.
- **Rounding-bias constant +0.4 s/call**: SF stores whole seconds;
  billed duration averages +0.4 s above it. One additive constant brings
  aggregate error from ~0.25% to ~0.05% (~$1.6/mo → ~$0.4/mo). Not
  worth more sophistication than that.
- **Management API: deferred.** The usage:read key works (verified) but
  the usage log lags hours behind transcription — unusable for
  "cost alongside each run" regardless. Documented trigger to revisit:
  **as soon as the client transitions to a Deepgram plan** (rate
  changes → re-run `scripts/check_duration_match.py` with the usage key,
  update the constant; optionally store the usage key in `sp_setting`
  and build invoice reconciliation then).
- CSV: **both** granularities — per-run and per-task.

## What counts as a paid task

Deepgram charged us for a task iff audio was actually transcribed:

```
paid := status = 'completed' OR deepgram_request_id IS NOT NULL
```

- `completed` without request id = transcribed before the id column
  shipped — paid.
- `failed` WITH request id = Deepgram succeeded, S3/postprocess failed —
  paid (this is why the worker sets the id before postprocessing).
- `skipped`, `pending`, `failed` without id — not paid, $0.
- Skip rows with `skip_reason='already_transcribed'` are bookkeeping
  copies of a task paid for elsewhere — **excluded from cost** (their
  completed original carries it) to avoid double counting.

A paid task whose mirror row is missing or has NULL
`CallDurationInSeconds` is **uncosted**: excluded from the sum and
surfaced as a count (`uncosted_tasks`) so totals are honest, never
silently low. Expected to be rare (task is in the hourly stream sync).

## Server (udab-server)

### Billing constants + calc — new `app/services/deepgram_billing.py`

```python
# All values empirical, measured against Deepgram's Management API
# (response.details.usd) on 21 PROD requests, 2026-08-28, both vendors.
# Re-measure with scripts/check_duration_match.py and update when the
# client moves off pay-as-you-go onto a Deepgram plan.
RATE_USD_PER_BILLED_MINUTE = 0.004345
BILLED_CHANNELS = 2            # multichannel bills each channel
SF_ROUNDING_BIAS_SECONDS = 0.4 # sf_task stores whole seconds; billed avg +0.4s

def estimated_cost_usd(call_duration_seconds: int | float | None) -> float | None:
    """None in → None out (uncosted). Estimate, not invoice."""
```

Formula: `(duration + 0.4) / 60 * 2 * RATE`. Pure function, unit-tested.
Docstrings carry the provenance; this module is the single home for
every billing number — nothing else hardcodes a rate.

### Read service (`app/services/transcription_jobs.py`)

Two new query helpers (SQL aggregation, no per-row Python):

1. `cost_by_job(db, job_ids) -> dict[job_id, JobCost]` — one grouped
   query joining task rows (paid + not already_transcribed) to `sf_task`;
   returns per job: `paid_tasks`, `costed_tasks`, `uncosted_tasks`,
   `total_duration_seconds`. Cost in dollars is derived via
   `estimated_cost_usd` (bias applied per task: `SUM(duration) + 0.4 * costed_tasks`).
2. `summary_matrix(db, filters) -> …` — over the whole filtered set
   (same `created_at` range + `source` filters the list endpoint uses):
   task counts grouped by `(call_result, task status)`, job counts by
   job status, total cost + uncosted count via the same join.

### API (`app/routes/transcription_job.py`, JWT + VIEW_CALLS)

- `GET /transcription-jobs` (list): each item gains
  `cost: {estimated_usd, costed_tasks, uncosted_tasks}` (null cost when
  0 costed); `summary` gains `total_cost_usd`, `uncosted_tasks`,
  `jobs_by_status`, and `tasks_matrix` (call_result × status counts).
  Existing fields unchanged.
- `GET /transcription-jobs/export/runs.csv` — one row per run in the
  filtered set (no pagination): `id, requested_at, source, call_result,
  status, window_start, window_end, tasks_total, transcribed, skipped,
  failed, pending, estimated_cost_usd, uncosted_tasks`; final summary
  rows (blank line, then totals + the matrix). Filename
  `transcription-runs_{start}_{end}.csv`.
- `GET /transcription-jobs/export/tasks.csv` — one row per task:
  `job_id, requested_at, source, call_result, sf_task_id, status,
  skip_reason, vendor, duration_seconds, estimated_cost_usd,
  deepgram_request_id`. Vendor derived from `recording_url` host via the
  existing `detect_vendor`.
- Both exports accept the same `start_date`/`end_date`/`source` query
  params as the list. Streamed (`StreamingResponse`, stdlib `csv`) — the
  zoominfo `ReportWriter` is command/file-oriented, not reused; note the
  header-metadata idea (run params in first rows) borrowed from it.
- Guard: exports run unpaginated; bound the query to the filter range
  and log row counts. A month is ~10-40k task rows — fine to stream.

### Perf note

Month-scale: ~1k jobs / ~30k task rows joined to `sf_task` by unique
indexed `sf_id`. One grouped query per endpoint call; no N+1. The list
endpoint computes cost only for the current page's jobs plus one summary
query for the filtered set.

## Client (udab-client, `TranscriptionsTab.vue`)

- New **Cost** column per row: `$X.XX est.` (2 decimals; `—` when null;
  tooltip shows costed/uncosted task counts when uncosted > 0).
- Summary area grows from the current one-liner into a compact strip:
  total estimated cost for the filtered range, jobs by status, and the
  call-result × status task matrix (small table; appointment/pitch rows,
  status columns). All figures labeled "estimated".
- Two download buttons ("Runs CSV", "Tasks CSV") hitting the export
  endpoints with the current filters (the API helper must pass through
  as a file download — `window.location` with query string or fetch+blob,
  matching however the app does downloads elsewhere; transcripts use
  presigned URLs so this may be the first authed download — fetch+blob
  with the JWT header then).
- Bonus, only if trivial: MTD / last-7-days preset buttons beside the
  date picker.
- No Bootstrap JS (house rule) — plain markup + Vue.

## Tests

- `deepgram_billing`: formula (known values from the probe: 252.647 s →
  $0.0366 ballpark), None-in/None-out, constants sanity.
- Service: cost_by_job / summary_matrix with the FakeSession pattern —
  paid predicate (completed-without-id, failed-with-id in;
  already_transcribed copies and skipped out), uncosted counting on
  missing mirror row / NULL duration.
- Routes: list response shape (cost per item, summary fields), export
  endpoints (headers, row shape, totals block, filter passthrough,
  permission required) — existing compiled-SQL-assertion style where the
  query shape matters.
- udab-client: `npx vitest` stays green; component test optional.

## Out of scope (documented, not built)

- Management-API reconciliation & storing the usage:read key — until the
  client's plan transition (see Decisions).
- Storing cost or duration in our tables.
- Per-account / per-end-client cost attribution (would use Deepgram
  `tag` on /listen — noted for the future, not wired).
- Scheduling/emailing reports.

## Implementation plan

| Repo / file | Change |
|---|---|
| server `app/services/deepgram_billing.py` | new — constants + `estimated_cost_usd` |
| server `app/services/transcription_jobs.py` | `cost_by_job`, `summary_matrix` |
| server `app/routes/transcription_job.py` | list enrichment + 2 CSV export routes |
| server tests | billing unit, service, route/export tests |
| client `src/pages/calls/TranscriptionsTab.vue` | cost column, summary strip, 2 download buttons, (presets) |
| client tests | suite stays green |

## Resolved during implementation (2026-08-28)

1. `estimated_cost_usd(duration_seconds, task_count=1)` — the +0.4 s bias
   applies per task, so aggregate calls pass the costed-task count; every
   dollar figure still flows through the one function.
2. The `cost` object on list items is always present with
   `estimated_usd: null` when nothing costed — `uncosted_tasks` must
   survive exactly when cost can't be computed.
3. Exports accept all five list filters (`start_date`, `end_date`,
   `source`, plus `status`/`call_result` added to the list by PR #720)
   via one shared `_build_filters`.
4. CSV run-parameter metadata lives in the trailing summary block, not
   above the header — the file stays parseable as plain CSV.
5. tasks.csv cost cell: blank = paid but uncosted (no mirror duration);
   `0.0000` = never billed. The distinction keeps totals honest.
6. Rounding: 6 dp in the function (float noise only), 4 dp in CSVs,
   2 dp in the UI.
