---
kind: spec
status: done
area: transcription
updated: 2026-08-24
repos: [udab-server, udab-client]
summary: "auto-transcribe poller: per-minute scan of new Pipeline Client/Active Tasks, single-flight worker, claimed-exclusion."
---

# Automatic transcription — scheduled poller over the transcription pipeline

Status: IMPLEMENTED 2026-08-24 (uncommitted, branch `transcribe-proactively`
in udab-server + working tree in udab-client), pending review/deploy + the
EventBridge schedule (infra, outside repo). Client-facing questions answered
2026-08-24 — no open blockers. Implementation decisions taken during dev are
appended to Decided.

Client ask (email, 2026-08-23, paraphrased): turn on automatic
transcription ASAP, forward-looking. Scope = Tasks on Accounts with Record
Type **Pipeline Client** and Status **Active**, Call Result = **Pitches**
(`KDM Pitched`) and **Appointments incl. follow-ups** (their spelling-variant
list), both **CloudCall and Orum** URLs; if a CloudCall URL is missing,
fetch it and write it down ("to Aurora"); transcribe **as soon as possible
after the call completes and the Task is generated**.

Tomas's shape: a job that runs every 1–2 minutes on AWS Batch (like every
other scheduled command in `udab-server/app/commands`), looks back over a
short rolling window for untranscribed Tasks meeting the criteria, and
pushes them through the existing pipeline. The command is a thin caller;
the work is in what the pipeline's front half must learn to do.

## Open questions

Client-facing: **none.** Confirmed by Anna Clare Crews 2026-08-24
("Yes, thank you for catching those"): `Appointment Confirmation`,
`Appointment Confirmed`, and pitch follow-ups are all IN. See the
disposition list in Analysis for the exact values.

Internal (to settle at implementation, lean stated):

- [ ] **In-process vs. HTTP.** The poller can call our own
      `POST /api/transcription-jobs` over HTTP (needs base URL + API key in
      `sp_setting`, exercises the public contract) or call a shared service
      function in-process. Lean: in-process — same code path, no self-HTTP,
      no key plumbing.
- [ ] **Worker inline vs. dispatched.** The poller already runs in a Batch
      container; it could run the transcribe worker inline instead of
      `launch_job`-ing a second container per tick. With the single-flight
      rule (see Decided) the two are equivalent in throughput and latency —
      either way at most one worker runs and ticks during its lifetime
      enqueue nothing. Lean: dispatch — it keeps the poller's own Batch
      timeout (~600 s) meaningful as a hang-killer, whereas an inline
      worker would need the poller timeout raised to cover a full chunk,
      weakening that protection. Revisit only if container churn is noisy.
- [ ] **Retry policy for `failed` tasks.** A dead Orum URL (expired
      recording → Deepgram fetch error) would be re-attempted every tick
      forever if `failed` rows don't count as "claimed". Lean: exclude
      tasks with a `failed` row newer than N hours (N = 6?), so transient
      failures retry a few times a day and permanent ones stop costing
      attention. A fetch failure costs no Deepgram money, so the waste is
      logs, not spend.
- [ ] **Management UI.** Scheduled jobs will appear in the udab-client
      Transcriptions tab — with empty ticks suppressed, at most a few
      hundred small jobs/day. Tag them (`source=scheduled` in
      `filters_json`) so the UI can filter/group; decide whether the UI
      change ships in the same PR or after.

## Decided (2026-08-23, Tomas)

- **"Write the URL to Aurora" → stamp the `sf_task` mirror too.** The
  stamper (`stamp-cloudcall-urls`) keeps stamping Salesforce; in addition
  it runs `UPDATE sf_task SET Call_Recording_URL_Public__c = … WHERE sf_id
  = ? AND Call_Recording_URL_Public__c IS NULL` (update only, never an
  insert). Harmless by construction: SF holds the same value, so the daily
  sync rewrites it identically rather than clobbering it, and the
  transcription path reads SF live regardless. The 2026-07-30 rejection
  was of stamping the mirror *instead of* SF, not in addition.
  On its own the mirror stamp would rarely find a row: Tasks are not in
  the hourly `sfdc-stream` type list (`stream.py:28–44`, verified
  2026-08-23) and reach the mirror only via the daily full `sfdc sync`.
  Hence the next item.
- **`task` joins the hourly `sfdc-stream`.** One-line change: add
  `"task"` to `available_types` in `stream.py`; the stream already hands
  each type to `sfdc_sync_internal(type, additional_where=<LastModifiedDate
  window>)` and the full sync already knows `task`, so nothing else moves.
  Default window is `--hours 2`, so each run re-syncs Tasks modified in
  the last 2 h (~10k Orum + ~300 CloudCall Tasks/day org-wide ≈ a few
  hundred per run; the Orum 39-second dials dominate the count). Two
  effects: (a) new Tasks appear in the mirror within ≤ 1 h; (b) because
  our SF stamp bumps `LastModifiedDate`, the URL itself rides into the
  mirror on the next hourly run without any mirror stamp — the explicit
  mirror `UPDATE` above then only shortens the gap for Tasks already
  synced before their URL arrived. Net for the client: **URL in Aurora
  within roughly an hour of the call**, bounded by the stream interval,
  not by us. Say that plainly when answering "write the URL to Aurora";
  if they need minutes, the stream interval is the knob (a schedule
  change, not code).
- **Orum Tasks without a recording URL (~20%)**: nothing we can do (no
  Orum API). Out of scope, noted below; not a client question.
- **Run-rate OPEX**: noted (≈ the v2 ≈$700/mo ceiling for both vendors,
  reduced by the Pipeline-Client/Active filter, now a standing cost).
  Budget assumed; not raised with the client again.
- **Window and cap are internal design** (pre-existing Task volume is our
  constraint, not a client question). See Assumptions 1–2 and the cap
  section in Analysis.
- **Single-flight workers (2026-08-24, Tomas).** At most ONE scheduled
  transcription worker runs at a time. Without this, each non-empty tick
  dispatches its own worker and a slow patch (Deepgram degraded, calls
  riding the 10-min timeout) stacks them: 10 stuck minutes ≈ 10 workers ×
  5 concurrent Deepgram calls, plus that many containers crowding the
  `main` Batch queue. Enforcement is in the poller, not Batch config:
  before scanning, reconcile any in-flight scheduled jobs
  (`reconcile_batch_status` — cheap, 0–2 rows; also unsticks jobs whose
  container died), and if one is still `pending`/`processing`, **exit
  without creating anything** — unclaimed Tasks simply wait for a later
  tick. Throughput is not a concern: one worker at concurrency 5 and
  ~30 s/call clears ~600 Tasks/hour ≈ 14k/day, an order of magnitude
  above the ~1,200/day in-scope arrival rate, so a single worker always
  catches up within the day. The rule covers scheduled jobs only; manual
  on-demand POSTs still dispatch their own workers (rare, human-triggered,
  and they claim their Tasks so the poller won't double-feed them).

## Decided during implementation (2026-08-24)

- **In-process** (the lean): `transcription_jobs.create_job(...)` is the
  shared service; the route body is now validation + one call; the command
  calls it directly. Patch targets in tests moved accordingly
  (`transcription_jobs.launch_job`).
- **Dispatched worker** (the lean), one dispatch per tick. The poller scans
  appointment and pitch in an order alternating by minute parity (stateless
  rotation) and dispatches the first call result with pending work; the
  sibling gets the next free tick.
- **Failed-retry backoff**: `FAILED_RETRY_AFTER_HOURS = 6`.
- **`unsupported_vendor` skips claim permanently** — otherwise a sandbox
  Task (orum-playground) would get a fresh skipped row every tick forever.
  They are recorded once, in a dispatch-less job that is born `completed`.
- **Minor API behavior change**: a POST matching zero pending tasks still
  records its (possibly empty) job, but no longer dispatches a pointless
  worker — the job is `completed` on creation. Response shape unchanged.
- **JWT list route** gained `?source=scheduled|manual` (JSON_EXTRACT on
  `filters_json`; absent source = manual, covering pre-feature jobs); the
  udab-client Transcriptions tab gained a Source column + filter, shows
  record-type ids as names, and formats the new parameter badges.

## Assumptions (defaults that ship unless overridden)

1. **Date basis is `CreatedDate`**, not `Appt_Set_Date__c`. The existing
   endpoint's `startDate`/`endDate` filter on `Appt_Set_Date__c` (a Date,
   day granularity — chosen for the original on-demand "appointments set
   between X and Y" use case). "New Tasks" is a different field, and the
   v2 prod findings show follow-up Tasks mostly lack `Appt_Set_Date__c`,
   so the client's newly-included follow-ups would never surface under it.
2. **Rolling window, `CreatedDate >= now − L`, L default 24 h**
   (`--lookback-hours`). The poller's primary job is "new Tasks"; the
   window is insurance. Correctness needs only L > max URL-stamp delay
   (~15 min SF batch worst case) + one tick, because the whole window is
   re-scanned statelessly every tick: a Task created 09:00 whose URL is
   stamped 09:12 is picked up on the 09:12 tick since
   `Call_Recording_URL_Public__c != null` is re-evaluated against SF live
   each time. The rest of L buys: self-healing after a poller outage of
   up to L, and a hard bound on the go-live backlog (≤ 1 day of in-scope
   Tasks; run the first tick with `--lookback-hours 2` if even that is
   unwanted). Nothing older than L is ever considered — no 5-month
   backfill by accident. A rolling window has no midnight edge, so no
   "today/yesterday" logic.
3. **Criteria are fixed in code** for the scheduled run (Pipeline Client,
   Active, both call results, the disposition constants). Business rules
   are code constants, not `sp_setting` (v2 decision, unchanged).
4. **Disposition lists**: appointment bucket = existing 26 values + the 9
   follow-up variants from the client's list + live strays `Appt Follow
   Up`, `Appointment Confirmation`, `Appointment Confirmed` (confirmed IN
   2026-08-24). Pitch bucket = `KDM Pitched` + the pitch follow-up
   spellings (confirmed IN 2026-08-24) — still one `callResult` enum value
   `pitch`, not a new category.
5. **Both call results every tick** — either two scheduled invocations
   (one per `callResult`) or the service accepts both at once. One job per
   call result keeps `sp_transcription_job.call_result` single-valued as
   today; the poller just makes two calls.
6. **"Untranscribed" = no row for that SF Task in `completed`, `pending`,
   or `transcribing` status, and no `failed` row newer than N hours.**
   Completed-only (today's dedup) is not enough at 1-minute cadence — see
   Analysis.
7. **Empty ticks leave no trace**: no job row, no Batch dispatch, one log
   line. Overnight and most daytime minutes are empty.
8. **Scheduled runs do not persist `already_transcribed` / claimed skips.**
   The on-demand POST keeps recording them (the caller wants the full
   accounting); the poller would otherwise write ~1,000 junk rows per
   tick by late afternoon (~700k/day). `unsupported_vendor` skips are
   still recorded — they are information, and there are few.
9. **Caps never error on the scheduled path** — a query guard plus a
   per-chunk enqueue cap applied after exclusion; overflow waits for a
   later tick. Per-chunk cap ~200 (not 1,000): under single-flight it
   bounds one worker's lifetime to ~20 min, so during catch-up the poller
   regains control every chunk and freshly-arrived calls (newest-first
   ordering) jump into the very next chunk instead of queueing behind a
   100-minute mega-job. The on-demand POST keeps its `400` (see Analysis).
10. **Worker unchanged.** Deepgram params, post-processing, S3 layout,
    vendor handling, Orum `?raw=true`/channel swap — all as shipped in v2.
11. **Infra**: EventBridge → Batch schedule every 1 min (2 if container
    start latency makes 1 pointless), `attemptDurationSeconds` ≈ 600,
    `identical_batch_job_active()` at startup exactly like
    `stamp-cloudcall-urls`. Schedule definition lives outside the repo.

## Analysis

### What exists (and what the poller reuses as-is)

`POST /api/transcription-jobs` (`app/routes/api/transcription_job_api.py`):
queries SF live with `_build_soql`, snapshots matching Tasks into
`sp_transcription_job` + `sp_transcription_job_task`, dispatches
`transcribe-calls-job` to Batch. Filters today: `Appt_Set_Date__c` date
range, `CallDisposition IN (...)` from `CALL_DISPOSITION_MAP`,
`Call_Recording_URL_Public__c != null`, optional `Account.OwnerId`,
`Account.Owner.Partner_Sales_Team__c`, `AccountId`, `WhoId`.

Already covered by shipped work:

| Client clause | Covered by |
|---|---|
| CloudCall **and** Orum URLs | v2 slice 3 (#673): vendor by URL host, Orum `?raw=true`, channel swap |
| Missing CloudCall URL → fetch + write | `stamp-cloudcall-urls` (#685): every minute, SessionID/Leg==1 exact match, PATCHes the SF Task. Endpoint reads SF live, so it sees stamps within a tick. Mirror stamp added per Decided |
| Pitches | `pitch → ["KDM Pitched"]` |
| Appointments | 26 of the client's variants already in the constant |

### Gaps, ordered by how much they change the shape

**1. Date basis.** `Appt_Set_Date__c` cannot drive a near-real-time poll:
it is a Date (no intra-day window), a "today" window on it trips the
current 1,000 cap by mid-afternoon (June in-scope volume ≈ 26k
Tasks/month ≈ 1,200/workday before the Pipeline/Active narrowing), and
follow-ups largely don't carry it. Add a `CreatedDate`-based window to
the SOQL builder; the on-demand `Appt_Set_Date__c` range stays for the
POST.

`sp_transcription_job.start_date`/`end_date` are `NOT NULL` — the
scheduled job stores the window's calendar dates there; no migration
needed. If a cleaner `date_basis` column is wanted later, that's a
one-column migration.

**2. "Untranscribed" must be decided before any row is written — and it
must cover in-flight work.** Today's dedup is per record *during*
snapshotting and checks `status='completed'` only; the worker re-checks
the same. Two problems at 1-minute cadence:

- *Row noise*: every already-transcribed Task in the window gets a
  `skipped/already_transcribed` row in every tick's job.
- *Double spend*: a tick-N job is typically still running at tick N+1
  (≈30 s per call, concurrency 5). Its Tasks are `pending`/`transcribing`,
  not `completed`, so tick N+1 enqueues them again and both workers pay
  Deepgram for the same audio. The worker's own re-check has the same
  blind spot, so it doesn't save us.

Fix: after the SOQL returns, one query —
`SELECT sf_task_sf_id FROM sp_transcription_job_task WHERE sf_task_sf_id
IN (...) AND (status IN ('completed','pending','transcribing') OR
(status='failed' AND updated_at > now − N h))` — and drop those ids before
creating anything (chunk the `IN` at 1,000 ids). Index
`ix_transcription_job_task_sf_id_status` already serves it. The worker's
completed-only re-check stays as a last line.

Residual race: two pollers running concurrently could both pass the
pre-filter. `identical_batch_job_active()` makes that rare; the cost is
one duplicate Deepgram call, not a wrong transcript. Acceptable; a MySQL
advisory lock (`GET_LOCK('auto_transcribe', 0)`) closes it if we care.

**3. Caps.** The cap exists so nobody transcribes the universe by
accident; it is arbitrary and flexible, but for the scheduled path it
must (a) never make the job error out and (b) never be so low that a
day's Tasks can't be done within the day across ticks. Today the route
runs SOQL `LIMIT cap+1` and returns `400` if the cap is exceeded — right
for a human caller who should narrow filters, wrong for a poller.

There is also a trap in reusing one number: the SOQL `LIMIT` runs
*before* our claimed-exclusion. Late on a busy day a 24 h window holds
~1,200 matches of which ~1,190 are already done; `LIMIT 1000` could
return only done rows and starve the few new ones. Hence two numbers:

| | On-demand POST | Scheduled |
|---|---|---|
| SOQL | `LIMIT MAX_TASKS_PER_JOB + 1` | `ORDER BY CreatedDate DESC LIMIT MAX_SOQL_ROWS` (10,000 — "don't fetch the universe"; the window already bounds volume, so this only bites on a silly `--lookback-hours`; log loudly if hit). `query_all` is the alternative if pagination is preferred over a guard |
| Over cap | `400`, caller narrows filters | n/a at the query; after exclusion, enqueue at most `MAX_TASKS_PER_CHUNK` (~200), remainder deferred to a later tick, logged as `deferred=N`. Never an error |

`ORDER BY CreatedDate DESC` puts fresh calls first under either limit,
which is the latency promise; older deferred Tasks still drain because
capacity far exceeds arrivals. Under single-flight the chunk cap bounds
one worker's lifetime (~200 × 30 s / 5 ≈ 20 min), so a full-day backlog
(~1,200) drains in ~6 chunks ≈ 2 h while fresh calls keep preempting via
the ordering; throughput is set by the single worker (~600 Tasks/h ≈
14k/day), not by the cap.

**4. Empty ticks** — per Assumption 7. Mechanical once the pre-filter
exists: `if not pending: log; return`.

**5. Account criteria.** Two new SOQL predicates on the Task→Account
relationship (the builder already traverses it for `Account.OwnerId`):
`Account.RecordTypeId = '012A0000000kZfwIAE'` (`SfAccount.PIPELINE_CLIENT`,
the constant used by `sync_performance_metrics` and the strategy routes)
and `Account.Status__c = 'Active'` (same predicate
`sync_performance_metrics.py:315` applies on the mirror). Record-type IDs
over names for consistency with the codebase; a list in the API shape
(`accountRecordTypeIds`) since the client wrote "[Pipeline Client]" as a
list. Task `AccountId` is auto-derived from the Contact for WhoId Tasks,
so pitch Tasks carry it.

**6. Disposition list.** Append to `CALL_DISPOSITION_MAP[appointment]`:
`Appointment Follow-up`, `Appointment - Follow Up`, `Appointment Follow Up`,
`Appointment follow up with Todd`, `Appointment Follow - Up`,
`Follow up appointment`, `Follow-up appointment`,
`Appointment Follow-up reschedule`, `Appointment Folow-Up`,
`Appt Follow Up`, and — confirmed 2026-08-24 — `Appointment Confirmation`,
`Appointment Confirmed`. (`Appointment- Follow Up` is already present;
`Appointment Reschedule` is a duplicate in the client's list; the empty
string is dropped; SOQL comparison is case-insensitive so casing dupes
collapse.) This is the "eventually" from v2 slice 2, now due.

Append to `CALL_DISPOSITION_MAP[pitch]` (confirmed 2026-08-24):
`Pitch Follow - Up` (the dominant prod spelling, 1,184/90d),
`Pitch Follow-Up` (the client's spelling), `Pitch Follow Up` (spacing
variant — unverified in prod but free, same policy as the client's typo
list). Spacing differences are NOT collapsed by SOQL, only casing —
hence all three.

### Proposed structure

Factor the front half of the route into a service so both callers share
one SOQL builder, one exclusion step, one snapshot+dispatch:

```
services/transcription_jobs.create_job(
    db, criteria,               # dispositions, date window, account filters
    *, exclude_claimed: bool,   # pre-filter completed/in-flight/recent-failed
       record_skips: bool,      # persist already_transcribed rows?
       cap_mode: "reject" | "truncate",
       source: str,             # "api" | "scheduled" → filters_json
) -> JobSummary | None          # None when nothing to do
```

- `POST /transcription-jobs` → `create_job(..., exclude_claimed=False,
  record_skips=True, cap_mode="reject", source="api")` plus the three new
  optional request fields (`createdSince`, `accountRecordTypeIds`,
  `accountStatus`). Behavior for existing callers unchanged.
- `app/commands/auto_transcribe.py` → guard, build the fixed criteria for
  each call result, `create_job(..., exclude_claimed=True,
  record_skips=False, cap_mode="truncate", source="scheduled")`, log
  counts. ~40 lines.

### The command

`auto-transcribe` (typer, registered in `commands/__init__.py`):

1. `identical_batch_job_active()` → exit if another tick is running.
2. **Single-flight gate**: reconcile in-flight scheduled jobs
   (`reconcile_batch_status`), then exit if one is still
   `pending`/`processing` — log `worker busy (job N)`.
3. Deepgram key present? (same up-front check as the route; exit loudly
   if not — don't create doomed jobs every minute).
4. For `callResult in (appointment, pitch)`: `create_job(...)`. Both
   results share the one worker slot — either two jobs handed to one
   dispatch, or appointment first and pitch next tick; decide at
   implementation (latency difference is one tick).
5. Log one summary line per tick: `matched=… claimed=… pending=…
   deferred=… dispatched_job_id=…` (or `nothing to do` / `worker busy`).

CLI options for manual runs: `--dry-run` (SOQL + exclusion, log what would
be enqueued, no rows, no dispatch), `--lookback-hours` (default 24).

### Volume, latency, cost

- SOQL per tick: one query per call result, ≤ ~1,200 rows on the busiest
  day. Trivial for SF API limits (2 calls/min).
- Latency from call end to transcript: CloudCall — URL stamped 1–2 min
  post-call by the stamper (15 min worst case via the SF batch) + ≤1 min
  tick + ~30 s Deepgram. Orum — URL present at Task creation + ≤1 min
  tick + Deepgram. Single-flight adds at most one chunk (~20 min) when a
  catch-up backlog is draining — and newest-first ordering puts fresh
  calls at the head of the next chunk. Both comfortably inside "as soon
  as possible", and well inside Orum's days-scale retention window.
- Deepgram: same per-minute cost as v2; total bounded by in-scope volume
  under Pipeline-Client/Active, i.e. ≤ the v2 ≈$700/mo ceiling. Peak
  concurrency is 5 Deepgram calls (one worker) plus any manual on-demand
  job — far below Deepgram's limits.
- Batch: one poller container per minute + at most ONE scheduled worker
  container at any moment (single-flight). If poller churn is a problem,
  see the inline-worker question.

### Observability

Per-tick log summary (counts above). Worth a CloudWatch metric filter on
`pending=` / `deferred=` to see the pipeline breathe, and an alarm on the
poller failing to start for >15 min (the stamper has the same need).

### Out of scope

- Backfill of anything before go-live beyond the rolling window
  (forward-looking, as v2).
- **Orum Tasks without a recording URL (~20% of Orum Tasks)** — no Orum
  API exists; they are untranscribable and stay so. The "fetch the URL"
  clause of the client's ask is CloudCall-only.
- Refreshing expired CloudCall URLs.
- Any change to Deepgram params, post-processing, or storage.
- Per-end-client keyterms (still waiting on a source, v2).
- Replacing the on-demand POST — it stays for ad-hoc runs and testing.

## Implementation plan (when we code it)

| File | Change |
|---|---|
| `app/schemas/transcription_job.py` | append follow-up variants; optional `createdSince`, `accountRecordTypeIds`, `accountStatus` on the request |
| `app/services/transcription_jobs.py` | `create_job(...)` — SOQL builder moved here, `CreatedDate` window + `ORDER BY CreatedDate DESC`, account predicates, claimed-exclusion query, `cap_mode`, no-op on empty, snapshot + dispatch |
| `app/routes/api/transcription_job_api.py` | route body becomes validation + `create_job` call |
| `app/commands/auto_transcribe.py` | **new** — guard, fixed criteria, two `create_job` calls, summary log, `--dry-run`, `--lookback-hours` |
| `app/commands/stamp_cloudcall_urls.py` | also `UPDATE sf_task … WHERE sf_id = ? AND url IS NULL` after a successful SF PATCH (per Decided); count `mirror_updated` |
| `app/commands/sfdc/stream.py` | add `"task"` to `available_types` (per Decided) |
| `app/commands/__init__.py` | register |
| `tests/test_transcription_job_routes.py`, new `tests/test_auto_transcribe.py` | SOQL builder (new predicates, date basis, ordering), exclusion (completed / pending / recent failed / old failed), cap modes (reject vs truncate + deferred count), empty-tick no-op, single-flight gate (busy worker → no job created; stuck job reconciled then proceeds), route behavior unchanged; stamper mirror update hit/miss |
| udab-client (optional, follow-up) | show `source` in the Transcriptions tab, filter scheduled vs. manual |
| Infra | per-minute EventBridge → Batch schedule, `attemptDurationSeconds` ≈ 600 |

Order: `stream.py` one-liner (independent, can ship first) → schema
constant + service extraction (route keeps passing its tests) → exclusion
+ new predicates + cap modes → command → stamper mirror update → infra.
