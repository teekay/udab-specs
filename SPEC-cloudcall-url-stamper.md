# CloudCall URL Stamper — timer job, stamps Salesforce

Status: DRAFT 2026-08-03; blockers resolved same day (lives in
udab-server; SF creds via the existing `sp_setting` admin pattern) —
ready for dev once the CloudCall-credential prerequisite lands.
"Lambda" is the client's word for it; it lands as a udab-server
scheduled command, same as every other job.

## Goal

`Task.Call_Recording_URL_Public__c` populated within ~1–2 minutes of the
recording becoming available on CloudCall, instead of waiting for the
client's Salesforce batch (runs `:00/:15/:30/:45`, so worst-case ~15 min
after the call). CloudCall has the recording 1–2 min post-call
(`cloudcall-api-notes.md`).

A timer-triggered function scans for recent CloudCall Tasks with no
recording URL, resolves the URL from the CloudCall API, and **stamps it
onto the Task in Salesforce** — not onto the local `sf_task` mirror.

## History — why this shape, and why now

- The client CTO proposed a scanner/stamper lambda. The variant that
  stamps the **local mirror** was assessed and rejected 2026-07-30
  (SPEC-transcription-v2, "Implementation direction"): the mirror is
  stale, nothing reads a mirror stamp, and the sync clobbers it.
  Fetch-on-demand inside the udab job flow was chosen instead.
- Context changed 2026-08-03: the on-demand endpoint work is suspended
  (the client's other team is shipping their own tooling faster than we
  deliver prod-worthy code), and the client wants the URL on the Task
  itself as early as possible. Decision (Tomas): build the CTO's lambda,
  in the shape that actually works — **stamp Salesforce, not the
  mirror**. The slice-4 fetch-on-demand design remains valid if the
  endpoint work resumes; this lambda simply shrinks the URL-less
  population it would ever see.

Stamping SF dissolves all three 2026-07-30 rejection reasons:

| Rejection (mirror variant) | SF-stamping variant |
|---|---|
| Mirror is stale; scan loses the race | Scan queries SF live (see below — the mirror is unusable for this anyway) |
| Nobody reads the stamp (POST queries SF live) | Every consumer reads SF: udab's POST endpoint, the client's own tooling, and the mirror via normal sync |
| Sync clobbers local stamps | SF is ground truth; the stamp *is* the record. `sfdc sync` faithfully mirrors it back |

## Why the scan must query Salesforce live (code-verified 2026-08-03)

Two independent reasons the local `sf_task` mirror cannot be the scan
source:

1. **Task sync cadence is too coarse.** The hourly incremental
   `sfdc-stream` does **not** include `task` in its type list
   (`udab-server/app/commands/sfdc/stream.py:28–44`), and its `--types`
   option validates against that list, so it can't be forced. Tasks sync
   only via the full `sfdc sync`, which runs **daily** (confirmed
   2026-08-03) — mirror freshness for tasks is up to a day, not minutes.
2. **A mirror stamp would be clobbered anyway.**
   `process_task_record` (`sync.py:1330`) unconditionally `setattr`s
   every synced field from the SF record — a locally stamped URL reverts
   to NULL on the next sync until SF itself carries the value.

Both also hold for the *output* side: the mirror is neither scanned nor
written. This feature touches SF and CloudCall only.

## Architecture

**Home: udab-server** (decided 2026-08-03 — no separate repo; reuse the
existing `Sfdc` service and job conventions). A new typer command (e.g.
`app/commands/stamp_cloudcall_urls.py`) plus a small CloudCall client
service (`app/services/cloudcall.py` — auth + windowed calls listing),
scheduled every minute via the same EventBridge→Batch mechanism as the
other jobs, guarded by `identical_batch_job_active()` so overlapping
ticks no-op. If per-minute Batch container churn proves noisy, the
command can loop internally (scan every 60 s for N minutes per
invocation) — an ops tweak, not a design change.

**Deliberately NOT a real AWS Lambda** (decided 2026-08-03): a Lambda
would need its own IaC, VPC/NAT config, IAM role, and deploy pipeline
next to the existing Batch pattern — infrastructure we'd build before
shipping anything. The Batch command reuses everything and gets the
prototype into PROD faster. If per-minute Batch tick latency ever
disappoints, a container-image Lambda from this same repo is a clean
second step (the handler would be a thin wrapper around the same
command code), not a rewrite.

CLI options (both usable for manual runs, neither read by the
schedule, which runs bare):

- `--dry-run` — full scan + CloudCall resolution, log what would be
  stamped, **no SF writes**. Logs each Task id with the resolved URL's
  scheme+host+path (the numeric call id, for cross-checking against the
  Task) — the query string, which carries the signed token, is never
  logged.
- `--lookback-minutes` — candidate/listing window, default **120**.

Each tick:

1. **Candidates** — SOQL against SF live:

   ```sql
   SELECT Id, synety__Call_Session_Id__c, synety__Actual_Date_Time_of_Call__c
   FROM Task
   WHERE synety__Call_Session_Id__c != null
     AND Call_Recording_URL_Public__c = null
     AND synety__Actual_Date_Time_of_Call__c >= :now - LOOKBACK
   ```

   `synety__Call_Session_Id__c != null` *is* the CloudCall marker (only
   CloudCall tasks carry it) and simultaneously the join key. Orum is
   structurally excluded (no session id, and no API anyway).

   `LOOKBACK`: `--lookback-minutes`, default **120**. Long enough to
   keep retrying calls whose recording isn't up yet and to ride out a
   CloudCall/SF outage; short enough to bound the CloudCall window
   query. Older URL-less tasks are the SF batch's problem, as today.
   Zero candidates → exit without touching CloudCall (the common case).

2. **Resolve** — one CloudCall auth (`POST /v3/auth/login`, customer
   tier), one calls listing covering the same window, **without any
   `leg` parameter** (per the corrections in `cloudcall-api-notes.md`;
   `leg=c` returns the wrong leg). Group records by `SessionID`.

   Per candidate, the verified exact rule (SPEC-transcription-v2 slice 4,
   verified 10/10 on 2026-07-30):

   ```
   match:  SessionID == synety__Call_Session_Id__c
   select: exactly one record with Leg == 1, CallRecordingAvailable == true
   ```

   Anything else — no session match, 0 or 2+ leg-1 records, recording
   not yet available — **skip; the task is retried next tick and the SF
   batch remains the backstop**. Never closest-wins, never leg-2 (its
   recording is different audio, and the channel-labeling pipeline was
   verified on leg-1 only). Hard requirement unchanged: a wrong stamp is
   worse than no stamp, and this rule makes wrong stamps impossible by
   construction.

3. **Stamp** — `PATCH /sobjects/Task/{Id}` with
   `Call_Recording_URL_Public__c = CallRecordingURL`. The stamped host
   is `api.us.cloudcall.com` (PoC-verified), so udab's vendor detection
   and the existing pipeline consume it unchanged.

Idempotent by construction: a stamped task no longer matches the
candidate query. Every failure mode degrades to the status quo (batch
stamps within ~15 min), never below it.

## Concurrency — overlapping ticks

A tick can outlive the 1-minute interval. Three layers, in order of
load-bearing-ness:

1. **Correctness never depends on exclusion.** No cursor or local
   state; each tick re-derives from SF live. The candidate query
   excludes stamped tasks; the match rule is deterministic, so two
   concurrent ticks resolve a Task to the same leg-1 record and the
   second PATCH just rewrites an equivalent URL. Overlap wastes API
   calls, it cannot mis-stamp or lose work.
2. **Existing guard**: `identical_batch_job_active()`
   (`app/services/aws.py:140`) at startup, as in sfdc-stream. Known
   gaps, both acceptable: fails open on AWS API errors (→ benign
   overlap, per 1), and simultaneous STARTING jobs can both exit (→ one
   skipped tick, healed next minute).
3. **Optional hard guarantee**: MySQL advisory lock at startup —
   `SELECT GET_LOCK('stamp_cloudcall_urls', 0)`, exit on miss.
   Race-free, auto-released on connection death. Add it if we prefer a
   guarantee over a probability; skip it if layer 1 is deemed enough.

Ops hygiene: Batch job timeout (`attemptDurationSeconds` ≈ 600) so a
hung container gets reaped rather than lingering. (A "real" AWS Lambda
would solve overlap via reserved concurrency = 1 — same one-at-a-time
knob, different spelling; no reason to leave udab-server for it.)

## Race with the client's SF batch

Benign by design: both writers produce a working URL for the same
recording. Sub-cases to confirm with the client, not blockers:

- Does their batch overwrite a non-null `Call_Recording_URL_Public__c`,
  or skip it? Either is acceptable (overwrite = token refresh; skip =
  our stamp stands).
- `Call_Recording_URL_Changed__c` / `URL_Expiry_Time__c`: the batch
  populates expiry for 95% of CloudCall tasks. Whether the lambda must
  also stamp expiry depends on whether anything downstream requires it
  non-null — see open questions. Lean: don't stamp what we'd have to
  guess (token lifetime is "~30 days", not documented); let the batch
  fill it in if it overwrites, otherwise leave null.

## Credentials & security

- Salesforce: **resolved** — the job uses the existing `Sfdc` service,
  which loads the admin credentials from `sp_setting`
  (`SFDC_PROD_USERNAME`/`SFDC_PROD_PASSWORD`, `sfdc.py:76–91`), the same
  pattern as every job that writes to SF. Stamping is
  `Sfdc.update_object("Task", id, {...})`.
- CloudCall: `CLOUDCALL_LICENSE_KEY` / `CLOUDCALL_USERNAME` /
  `CLOUDCALL_PASSWORD` move from `.env` to `sp_setting` (site
  integration credentials — long-standing prerequisite from
  SPEC-transcription-v2; this job makes it due). New `Setting` keys +
  seed. Today the login is personal (`cgooding@`); the ask for a
  dedicated customer-tier API user becomes more urgent once a
  production timer depends on it.
- Never log CloudCall auth responses or recording URLs (both carry
  secrets/tokens) — status codes and counts only. Applies to CloudWatch.

## Observability

Per-tick counters to CloudWatch: candidates, stamped, skipped
(recording-not-ready / no-session-match / ambiguous-legs), errors.
Alarm only on sustained error rate; skips are normal (freshly-ended
calls take a tick or two).

## Volume

PoC 2026-07-30: a 3 h window returned 2,819 calls ≈ 16 calls/min
org-wide. Per tick: 1 auth + 1 listing + a handful of PATCHes. Both
APIs see trivial load; Deepgram cost is unaffected (this stamps URLs,
it doesn't change what gets transcribed).

## Out of scope

- Orum — no API; unchanged reliance on the SF field.
- Refreshing **expired** CloudCall URLs (30-day shelf life). The same
  resolution path could do it, but it widens the candidate query far
  beyond the 2 h window and the on-demand flow (slice 4 step 4) is the
  better home if that need materializes.
- Any change to udab-server, the mirror, or the transcription job flow.
  This is deliberately additive infrastructure.

## Implementation plan

| File | Change |
|---|---|
| `app/services/cloudcall.py` | **New** — auth (`/v3/auth/login`, customer tier) + windowed calls listing (no `leg` param), creds from `sp_setting` |
| `app/commands/stamp_cloudcall_urls.py` | **New** — candidate SOQL, SessionID/Leg==1 match, PATCH stamp, counters; `identical_batch_job_active()` guard; `--dry-run`, `--lookback-minutes` (default 120) |
| `app/models/setting.py` | New `Setting` keys for the three CloudCall credentials |
| `migrations/versions/…_add_cloudcall_credential_settings.py` | Seeds the three empty `sp_setting` rows (`password` type for the password), per house convention |
| `tests/…` | Match-rule unit tests from fixture CloudCall payloads (happy path, no match, 2+ leg-1, recording unavailable); command test with mocked `Sfdc`/CloudCall |
| Scheduling | New per-minute EventBridge→Batch schedule (infra, outside repo) |

## Open questions (none blocking)

- [x] ~~Where does it live?~~ udab-server (2026-08-03).
- [x] ~~SF write credential?~~ Existing `sp_setting` admin creds via the
      `Sfdc` service — the established pattern for SF-writing jobs.
- [ ] Does the client batch overwrite non-null URLs? (Benign either
      way; determines whether `URL_Expiry_Time__c` self-heals.)
- [ ] Should the job stamp `URL_Expiry_Time__c`? Only if a consumer
      requires it non-null; check their scorecard/QA tooling.
- [ ] Probe: does `ng-api` support filtering the calls listing by
      session id (PortaOne accepts `h323_conf_id`)? Would replace the
      window listing with exact lookups. Nice-to-have, not blocking.
- [ ] Dedicated customer-tier CloudCall API user (pre-existing risk,
      now production-critical).
