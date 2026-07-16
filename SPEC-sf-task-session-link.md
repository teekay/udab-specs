# Linking Salesforce Call Tasks to Talk Track Sessions

## Problem

When a rep dials a prospect through Orum, Salesforce receives a call Task record
(created by the synety/CloudCall integration). Separately, uDab records a Talk Track
session when the rep opens the prospect's details in Orum and works the talk track.
The two records are not connected: someone looking at the SF Task cannot tell which
Talk Track session accompanied that call.

The client wants each call Task stamped with the ID of the Talk Track session that
was active for that call.

## Solution

A daily batch job (AWS Batch, same pattern as `sfdc-stream`) that:

1. Selects recent Talk Track sessions that represent an actioned Orum call
2. For each, finds the matching SF Task by dialed phone number + time window
3. Writes the session ID into the Task's `Talk_Track_Session_Id__c` custom field

No local state is kept. Idempotency comes from Salesforce itself: the job only
considers Tasks where `Talk_Track_Session_Id__c` is empty, so a Task is never
stamped twice, and re-running the job over the same period is harmless.

## Matching Model

Verified against production SF (PoC, July 2026):

- Orum calls produce Task records with `TaskSubtype = 'Call'` and the dialed number
  in the custom field `synety__To__c` (10 digits, no country code, e.g. `2244750683`).
  The number is *not* available in any standard Task field — only embedded in
  `Subject`/`Description` text and in `synety__To__c`.
- `WhoId` on the Task points to the Contact; our session has `contact_id`.
- One call = one Task. A rep calling twice produces two Tasks — and, since each call
  means re-opening the prospect in Orum, normally a fresh session too.
- Many sessions are noise: the session is created whenever the talk track iframe
  loads. Only sessions with `dialed_number` set represent actual dial actions.

### Match criteria

For each candidate session, find SF Tasks where **all** of:

| Task field | Condition |
|---|---|
| `TaskSubtype` | `= 'Call'` |
| `synety__To__c` | `=` session's `dialed_number` (normalized, see below) |
| `WhoId` | `=` session's `contact_id` |
| `CreatedDate` | within `[session.created_at, session.created_at + 1 hour]` |
| `Talk_Track_Session_Id__c` | empty |

Rationale for the window: the session starts when the rep opens prospect details;
the Task is created when the call ends. The rep may dial immediately or minutes
later — 1 hour is generous but the phone + contact + unstamped filters carry most
of the selectivity.

If the query returns more than one Task (rep redialed the same number within the
hour), take the **earliest** unstamped Task. Process sessions in `created_at`
order within a run, so successive sessions claim successive Tasks. Log these
multi-match cases; they should be rare.

If it returns zero, log and skip — the session stays unmatched forever (acceptable;
there is no retry state to manage, and the next day's window no longer covers it).

### Phone format

Both sides store the number the same way: digits only, no country code, no
formatting (verified during PoC testing). Match with direct equality — no
normalization layer. If unmatched-session counts come back unexpectedly high
after launch, format drift is the first thing to check.

## Session Selection

```sql
SELECT ... FROM sp_talk_track_session
WHERE dialed_number IS NOT NULL
  AND contact_id IS NOT NULL
  AND origin = 'orum.com'
  AND created_at >= NOW() - INTERVAL :hours HOUR
```

- `origin` stores the hostname parsed from the iframe referrer
  (`app/routes/talk_track.py`); verified in dev data as exactly `orum.com`.
- Known data-quality issue (seen in dev): some sessions carry a malformed
  `dialed_number` that looks like two numbers concatenated
  (e.g. `9294515686116467870784`). These will simply never match and land in the
  unmatched-count log. If frequent in production, fix the capture bug in the
  extension rather than adding fuzzy matching here.
- Sessions without `contact_id` are skipped in v1 (can't use the `WhoId` guard).
  Log a count so we know if this bucket is significant.

## Job Shape

- **Command**: `app/commands/sfdc/link_talk_track_sessions.py`, typer command
  `sfdc_link_talk_track_sessions`, registered in `app/commands/__init__.py` —
  same layout as `sfdc/stream.py`.
- **Guard**: `identical_batch_job_active()` check at start (as `sfdc_stream` does).
- **Options**:
  - `--hours` (default **26**): session lookback window. Cadence is daily; the
    2-hour overlap absorbs schedule drift and SF/session clock skew. If a run
    fails, rerun with a larger `--hours` (e.g. 50) — idempotent by design.
  - `--dry-run`: perform selection and matching, log intended updates, write nothing.
- **SF access**: existing `Sfdc` service with settings-table credentials
  (production service account). Matching query via `sfdc.query()`; update via
  `sfdc.patch_object("Task", task_id, {"Talk_Track_Session_Id__c": str(session.id)})`.
- **Batching**: sessions per day are low (hundreds at most); one SOQL query per
  session is acceptable. If volume grows, batch by querying all candidate Tasks for
  the window in one SOQL `IN (...)` query and match in memory.
- **Logging**: per run, summary counts — sessions considered, matched & updated,
  multi-match, unmatched, skipped (no contact), errors. This is the only
  observability for the first weeks; make it easy to grep.
- **Deployment**: AWS Batch scheduled job, 1× daily. Exact schedule TBD; running
  after the sales day ends (e.g. 03:00 UTC) keeps the window aligned with "yesterday's
  calls".

## What Gets Written

`Talk_Track_Session_Id__c` = the numeric `sp_talk_track_session.id`, as a string.

The client currently has no way to dereference this ID. If they ever need session
details, we expose a read endpoint later (e.g. `GET /talk-track/session/{id}` with
API-key auth — out of scope here). Chosen over `client_session_key` (UUID) because
the numeric ID is our primary key and shorter; revisit if the client wants an
opaque identifier instead.

We do **not** store the Task ID on our side. The SF-side empty-field filter is the
idempotency mechanism; local state adds a column and a failure mode without a
consumer. If a future feature needs the reverse link, add `sf_task_id` then and
backfill by re-running the matcher.

## Prerequisites / To Verify Before Implementation

- [ ] `Talk_Track_Session_Id__c` exists on Task in production (client says created;
      verify with a `FIELDS(CUSTOM)`-style query or a describe call), and its type/
      length fits a stringified integer.
- [ ] Ask client whether writing to Task triggers SF automations (flows, triggers,
      field history); a daily batch of hundreds of updates should not fire side
      effects.
- [x] ~~Confirm exact `origin` value~~ — verified: `orum.com`.
- [x] ~~Confirm `dialed_number` format~~ — 10-digit digits-only on well-formed rows;
      malformed concatenated values exist (see Session Selection note).
- [ ] Sample check: do Orum Tasks reliably carry the right `WhoId`? (Both PoC
      records did.)

Assumed (not gated on): the production admin service account can edit Tasks owned
by other users — consistent with existing SF write use cases. Iterate if wrong.

## Out of Scope (v1)

- Reverse link (`sf_task_id` on session)
- Endpoint for the client to fetch session data by ID
- Matching sessions that lack `contact_id`
- Real-time (per-call) stamping — batch cadence is the requirement
