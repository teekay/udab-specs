# Unmatched Report for the SF Task ↔ Talk Track Session Link Job

Follow-up to `SPEC-sf-task-session-link.md` (implemented as
`app/commands/sfdc/link_talk_track_sessions.py`).

## Problem

The link job reports only summary counts (`unmatched=N`, `skipped_malformed=N`).
The client is asking *why* specific Tasks are not linked, and counts can't answer
that. We need a per-record report with a diagnosed reason for every unlinked
record.

The job is session-driven, so "unmatched" today means *sessions* that found no
Task. The client's question is the reverse — Task-centric — and some Tasks are
unlinked for reasons no session-side row can show (the rep never opened the talk
track; the session had no `contact_id`; the captured number was malformed). The
report therefore covers **both sides**.

## Solution

Each run writes two CSVs to S3 and prints their full paths in the run summary:
a per-record detail file and a high-level summary (see Summary CSV below):

```
s3://abstrakt-intelligence/talk-track-session-sync/<YYYY-MM-DD>.csv
s3://abstrakt-intelligence/talk-track-session-sync/<YYYY-MM-DD>-summary.csv
```

- Date is the run's start date, UTC, ISO format (e.g. `2026-07-31.csv`).
- A same-day rerun **overwrites** the file. Fine: the job is idempotent and a
  later run has strictly fresher information.
- A clean run still writes the file (header row only) — a deterministic daily
  artifact distinguishes "nothing unmatched" from "job didn't run".
- Reuses existing plumbing: `create_csv()` from `app/services/csv_writer.py`
  and `write_s3_file()` from `app/services/aws.py`. No new services.
- Bucket and prefix are module constants (client-specified values, single
  consumer — not `sp_setting` material).

## CSV Format

One row per unlinked record, either side. Columns:

| Column | session rows | task rows |
|---|---|---|
| `kind` | `session` | `task` |
| `reason` | see reason tables below | see reason tables below |
| `session_id` | the unmatched session | nearest-miss candidate session, if any |
| `session_created_at` | ″ | ″ |
| `dialed_number` | session's number (10-digit) | Task's `synety__To__c` normalized to 10 digits (blank if unparseable) |
| `contact_id` | session's `contact_id` | Task's `WhoId` |
| `user_id` | session's `user_id` | Task's `OwnerId` |
| `task_sf_id` | nearest-miss candidate Task, if any | the unlinked Task |
| `task_created_date` | ″ | ″ |
| `detail` | short free-text explanation (e.g. "3 Tasks matched number, none matched contact") | ″ |

The "nearest-miss candidate" is the record that survived the most filter steps
before the set went empty (see diagnosis below) — it shows the client *which*
record almost matched and on what field it diverged.

## Summary CSV

The detail file answers "why is this record unlinked?"; the first question in
practice is "what's the breakdown?", and hundreds of detail rows are
overwhelming to start from. Each run therefore also writes
`<YYYY-MM-DD>-summary.csv` next to the detail file:

- Columns: `kind, reason, count` — one row per (kind, reason) pair that
  occurred, `session` rows first, then within each kind by `count` descending.
  Header-only when the run is clean.
- Pure aggregation of the detail rows already in memory — no extra queries and
  no new diagnosis logic. It is the logged reason histogram, materialized as a
  CSV the client can open (split by kind, which the log line flattens away).
- Same date stamp, overwrite-on-rerun, and dry-run semantics as the detail
  file; a failed upload of either file counts as an error (exit 1).

## Diagnosis — Session Side

**The matching loop and its queries are untouched.** The loop only *collects*
its failures as it goes (it already counts them): malformed sessions, errored
sessions (with the exception text), and unmatched sessions. After the loop, a
self-contained report pass diagnoses each unmatched session with one relaxed
diagnostic query (`sf_id, CreatedDate, WhoId, OwnerId, Talk_Track_Session_Id__c`
for `TaskSubtype = 'Call' AND synety__To__c = :sf_number AND CreatedDate` in the
window — no contact/owner/unstamped filters), then classifies in Python by
filtering stepwise: `WhoId` → `OwnerId` → unstamped → unclaimed. The step at
which the set becomes empty is the reason; the earliest survivor of the previous
step is the nearest-miss candidate.

Rationale: the report is expected to be discarded once trust is built, so all
report code — diagnosis, CSV build, upload — lives in one deletable block after
the loop, and the extra query cost lands only on unmatched sessions. Diagnosing
post-loop means the claimed set is final, so the claim-collision reason is
"claimed by *another* session this run" rather than "earlier" — diagnostically
equivalent (`sf_task` is static during the run and the claimed set only grows,
so no other reason can shift).

| Reason | Meaning |
|---|---|
| `malformed_dialed_number` | today's `skipped_malformed` bucket |
| `missing_contact_id` | session has a dial but no `contact_id` (see selection change below) |
| `no_task_in_window` | no Call Task with that number in the 1-hour window |
| `contact_mismatch` | Task(s) with the number exist in window, none match `WhoId` |
| `owner_mismatch` | number + contact match, none match `OwnerId` |
| `already_stamped` | matching Task(s) exist but all carry a `Talk_Track_Session_Id__c` |
| `claimed_by_another_session` | all matching Tasks were claimed by other sessions this run |
| `processing_error` | today's `errors` bucket (exception text goes in `detail`) |

For `no_task_in_window` only, run one extra probe: the same-number Call Task
with `CreatedDate` closest to the session, any time. If found, it goes in the
candidate columns — this is the direct detector for the clock-skew/format-drift
concern flagged in the original spec, without adding a config knob.

### Sessions without a contact

The main session select is **not** widened. Instead, the report pass runs its
own additive query for the contact-less bucket (`dialed_number IS NOT NULL AND
contact_id IS NULL AND origin = 'orum.com' AND created_at >= :cutoff`). These
sessions get session rows with reason `missing_contact_id` and feed the
task-side diagnosis. This restores the visibility the original spec asked for
("log a count so we know if this bucket is significant" — lost when the
implementation filtered them in SQL) without touching the matching query.

## Diagnosis — Task Side

After the session loop, select local Tasks that *should* have been linked but
weren't:

```
TaskSubtype = 'Call' AND synety__To__c IS NOT NULL
AND CreatedDate >= :cutoff
AND Talk_Track_Session_Id__c IS NULL
AND sf_id NOT IN (claimed this run)
```

`synety__To__c IS NOT NULL` is the proxy for "CloudCall/Orum-originated" —
manually logged calls have no expectation of a session and are excluded.

Diagnose each against the run's already-loaded session list — in memory, no
extra queries. Stepwise: number → contact present → contact match → owner match
→ window → claimed, same nearest-miss rule as above.

| Reason | Meaning |
|---|---|
| `task_number_format` | `synety__To__c` doesn't match `^1\d{10}$` — the format-drift alarm |
| `no_session_for_number` | no Orum session with that number in the lookback (incl. rep never opening the talk track; a session with a *malformed* capture also lands here — its own session row shows the malformed value) |
| `session_missing_contact` | session(s) with the number exist but have no `contact_id` — the v1 skip bucket, now visible per-record |
| `session_contact_mismatch` | session contact ≠ Task `WhoId` |
| `session_owner_mismatch` | session rep ≠ Task `OwnerId` |
| `session_outside_window` | session exists but Task's `CreatedDate` falls outside `[session.created_at, +1h]` |
| `session_claimed_other_task` | the matching session claimed a different (earlier) Task |

Known edge: Tasks created in the first hour after `cutoff` may belong to a
session older than the lookback and will report `no_session_for_number` /
`session_outside_window`. Rare with the 26h/daily overlap; acceptable.

## Job Changes

- **Summary**: add `skipped_no_contact` and `tasks_unlinked` counts, log a
  per-reason histogram, and print both `s3://...` paths in the final
  `typer.echo` summary.
- **Upload failure** (`write_s3_file` returns `False`, either file): count as
  an error → exit code 1. SF stamping already happened; a rerun is harmless by
  design.
- **`--dry-run`**: build the report but don't upload (dry-run writes nothing
  anywhere); log the row count and reason histogram instead.
- **Code placement**: everything report-related sits after the matching loop in
  the existing command module; the loop itself only appends its failures to
  lists. Reason classification as pure functions — they take row/session lists,
  no I/O, unit-testable without SF or DB fixtures. Discarding the report later
  means deleting that block, its constants, and the collection appends.

## Performance

(Volumes verified against the original spec: hundreds of sessions/day.)

- Session side: the matching loop is unchanged (one strict query per session).
  The diagnostic query runs only for unmatched sessions, uses the leftmost
  prefix of `ix_sf_task_link_match` (`synety__To__c`), and returns a handful of
  rows per number. The nearest-Task probe runs only for `no_task_in_window`
  sessions on the same index.
- Task side: one select over `ix_sf_task_created_date`; diagnosis is in-memory
  against the already-loaded session list.
- CSV is built in memory (hundreds of rows) and uploaded as a single string.

## Tests

Extend `tests/test_link_talk_track_sessions.py`:

- Existing match-scenario tests pass unchanged (the loop is untouched).
- One test per reason code, both sides, asserting reason + nearest-miss
  candidate columns.
- Summary CSV: aggregation from mixed detail rows (kinds split, counts right,
  ordering), and the `-summary.csv` key alongside the detail key.
- Upload failure → exit code 1; dry-run → no upload call.

## Out of Scope

- Client access to the CSV (presigned URLs, notification emails) — they get
  the path; delivery mechanism is theirs to request.
- Reporting *matched* rows — unmatched-only, per request.
- S3 lifecycle/retention for the prefix — revisit if the bucket owner cares.
- Retrying previously unmatched sessions — unchanged from v1.
