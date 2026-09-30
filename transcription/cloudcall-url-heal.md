---
kind: spec
status: draft
area: transcription
updated: 2026-09-18
repos: [udab-server]
summary: "Expired CloudCall URLs: one-off re-mint recovery (Software Advice) and the stamp-cloudcall-urls refresh/expiry patch."
---

# CloudCall URL heal — expired tokens, re-mint recovery, stamper refresh

Status: DRAFT 2026-09-18. Part A (one-off recovery) approved by Tomas and
**completed 2026-09-18** (485/489 recovered; results + gotchas below). Part B
(durable stamper patch) not yet approved — one open question to client IT
gates its scope. Findings below
were verified against prod (RO replica + live URL probes) on 2026-09-17/18.

## Problem

CloudCall recording URLs carry a signed token (`auth=` + `expiryDate=` query
params) minted for ~30 days. Something outside udab-server re-mints tokens on
a rolling basis — but only for Tasks it covers, and its selection appears to
key on `URL_Expiry_Time__c`. Tasks whose expiry was never stamped are
invisible to it, and their URLs die ~30 days after the call.

Verified facts (2026-09-17/18):

- **`URL_Expiry_Time__c IS NULL` ⇒ dead URL.** 8/8 sampled NULL-expiry tasks
  (June and early-August calls) returned HTTP 403; 3/3 future-expiry June
  controls returned 206 with `expiryDate=` re-minted to late September.
- **Scale: ~21k tasks Jan–Aug 2026** (~25–30% of each month's CloudCall
  tasks) are NULL-expiry ⇒ dead. September collapsed to ~0 NULL (12 tasks) —
  a client-side change around 2026-09-01, not yet confirmed durable.
- **udab-server audit (repo-wide):** the only SF-live writer of
  `Call_Recording_URL_Public__c` is `stamp-cloudcall-urls` (null-URL-only,
  120-min lookback, never revisits). **Nothing in the repo writes
  `URL_Expiry_Time__c` at all.** `sfdc-sync`/`sfdc-stream` mirror both fields
  SF→local only. So the rolling refresher and the expiry stamping live
  entirely in the client's SF org / their automation.
- Consequence: every stamper-stamped Task (no expiry stamp) is presumed
  invisible to the refresher and leaks ~30 days after its call — an ongoing
  self-inflicted trickle on top of the historical backlog.
- The recording itself is retained by CloudCall for 365 days (client IT
  claim, consistent with all probes) — a dead token is recoverable until the
  call's first birthday.
- `expiryDate=` in the URL query string equals the token expiry — expiry is
  computable by parsing the stored URL, no API call needed.

Affected consumers beyond transcription backfills: Appointment Calls page
audio playback (`resolve_playback`), appointment-email recording links.

## The re-mint resolution rule

For a Task holding a dead CloudCall URL the resolution is **deterministic**
(stronger than the stamper's session matching): the numeric call id in the
stored URL path (`/accounts/{acct}/calls/{id}/recordingurl`) equals the
CloudCall listing record's `id` (cloudcall-api-notes.md, field table; v2.md
slice-4 decision rule #1). Procedure:

1. Extract `{id}` from the dead URL.
2. `GET /v2/customers/{user}/calls?from&to` over a window around
   `synety__Actual_Date_Time_of_Call__c` (existing `app/services/cloudcall.py`
   client; batch several tasks per calendar-day window).
3. Match on exact `id` equality; sanity-check `Leg == 1` and
   `CallRecordingAvailable`.
4. PATCH `Task.Call_Recording_URL_Public__c` with the record's fresh
   `CallRecordingURL` (same single-field write the stamper does).

A wrong attach is impossible by construction (exact id). Misses are
`no_match` (call absent from listing) or `recording_unavailable` — skip,
never guess.

## Part A — one-off recovery (Software Advice backfill)

Goal: recover the ~489 Software Advice 2026 pitch-scope CloudCall tasks that
failed the 2026-09-17 backfill (see NOTES.md / the backfill session) and
deliver their transcripts as a standalone ZIP.

Runbook (local one-off script, scratchpad, no repo changes):

0. Re-derive target list from the RO replica (SA scope, CloudCall URL,
   untranscribed). Creds from `sp_setting`: `cloudcall-*`,
   `sfdc-prod-username`/`-password`. Same endpoints the server uses.
1. **Dry run**: resolve all targets per the rule above; report
   matched / no_match / recording_unavailable. No writes.
2. **Canary**: PATCH one task, curl-verify 206, re-POST a pitch job scoped to
   it (`POST /api/transcription-jobs`), confirm `sp_call_transcript` row.
3. **Bulk**: PATCH the rest (sequential, resumable, abort on first SF error);
   re-POST pitch jobs **scoped by `contactId`** (see gotchas — account scoping
   missed 47 tasks). Accepted noise: jobs re-attempt the contact's dead Orum
   tasks — fails free, extra failed rows.
4. ZIP exactly the target list (same per-file format as the main deliverable)
   + manifest with outcome per task (`recovered` / `no_match` /
   `recording_unavailable` / `patch_failed`). Expected Deepgram cost ~$10–15.

Results (run 2026-09-17/18): **485/489 matched and recovered (99.2%)**; 4
`no_match` (absent from CloudCall listings). All 485 SF PATCHes succeeded; the
re-minted URLs were still alive 24 h later (the `expiryDate=` param on
listing-minted URLs is honoured — ~30 days). Deepgram cost ≈ $10.

Gotchas learned the hard way — read before building Part B:

- **CloudCall listing 500s on a percent-encoded username in the path.** The
  username contains `@`; `/v2/customers/{user}/calls` must receive it raw, as
  `app/services/cloudcall.py` does. `urllib.parse.quote` → HTTP 500, every time.
- **Full-day listings are heavy** (~20–40k records/day at this org's volume).
  Fine for a one-off with narrow filtering; a scheduled refresher should use
  the narrowest window that covers its candidates and cache only what it needs.
- **`Task.AccountId` is volatile for this client.** It derives from the
  contact's Account and Software Advice reshuffles contacts between its ~180
  sub-accounts continuously; 47/489 tasks changed account between the main
  backfill and the recovery run, so account-scoped re-POSTs silently missed
  them (SOQL simply didn't return the task — no rows, no errors). Target
  re-transcription by **`contactId` (WhoId — stable)**, never by a
  snapshotted AccountId. Account folders in any export are "as of export".
- **The API POST path does not apply claimed-exclusion** (`create_job` is
  called without `exclude_claimed`; the 6 h `FAILED_RETRY_AFTER_HOURS` gate is
  poller-only). Manual re-POSTs retry failed tasks immediately; the only dedup
  is "completed with a transcript key".
- Presigned transcript URLs can die before their 7-day TTL (S3 `ExpiredToken`
  from the server's STS session); download immediately after minting.
- Do not fan out Deepgram fetches against CloudCall aggressively: the CloudCall
  API returned 504s during the second listing sweep. Keep concurrency modest.

## Part B — durable fix: patch `stamp-cloudcall-urls` (no new job)

Constraint from Tomas: no separate "heal" job — extend the existing stamper.

1. **Minimal (do regardless):** when stamping a URL, parse `expiryDate=` from
   it and PATCH `URL_Expiry_Time__c` in the same write; mirror both fields.
   Closes the stamper's own leak by making its Tasks visible to the client
   refresher (assuming the selection hypothesis). Resolves the open TODO in
   cloudcall-url-stamper.md ("should the job stamp expiry?").
2. **Refresh selector (gated on Q1 below):** second candidate SOQL in the
   same tick — CloudCall-host URL AND (`URL_Expiry_Time__c` < now + N days
   OR (NULL AND CreatedDate older than ~25 days)) — resolve via the exact-id
   rule, PATCH URL + expiry. Per-tick cap (stamper-style truncate; backlog
   drains over days), ORDER BY newest-first, and a floor of
   `CreatedDate >= now − 364d` (older recordings are gone anyway).

Notes for implementation: candidate SOQL must page carefully (~21k backlog);
CloudCall listing load is one windowed GET per task-day — cap accordingly;
login is still cgooding's personal account (dedicated API user remains an
open ask, see NOTES.md).

## Open questions

- **Q1 (gates Part B #2):** what changed client-side ~2026-09-01 that took
  NULL-expiry to ~0, and will their refresher backfill the ~21k historical
  stragglers? If they own the backfill, #2 never needs to exist. Ask client
  IT; also settles whether their batch overwrites a non-null URL.
- Q2: confirm the refresher-selects-on-`URL_Expiry_Time__c` hypothesis
  (Part B #1 is correct regardless; only the urgency changes).
- Q3: dedicated customer-tier CloudCall API user (standing ask).
