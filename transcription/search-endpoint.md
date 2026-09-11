---
kind: spec
status: ready
area: transcription
updated: 2026-09-09
repos: [udab-server]
summary: "Transcript search for the call-quality team: filterable, cursor-paged GET /api/transcripts over produced transcripts."
---

# Transcript search endpoint (API-key surface)

Status: IMPLEMENTED 2026-09-09 (uncommitted, udab-server working tree,
pending Tomas's review): `app/routes/api/transcript_search_api.py` +
`tests/test_transcript_search.py` (12 tests, local-MySQL integration
pattern) + consumer doc `docs/transcript-search-api-howto.md` (written to be
handed to Claude by the non-developer consumer). The `team` filter is
deferred (see Scope Boundaries) — blocked
on a client answer about whose team it means. No open questions.

## Problem Frame

The call-quality team retrieves transcripts through job-shaped endpoints
(`GET /api/transcription-jobs/{id}`, `GET /api/sf-tasks/{id}/transcript`).
Since auto-transcribe went live (spec: `auto.md`), jobs are per-minute
scheduled artifacts with no meaning to them — they tried to evolve the
on-demand job flow into the retrieval tool it was never designed to be.

Client ask (email braindump, 2026-09-08, refined 2026-09-09): a search
endpoint over produced transcriptions, filterable by account, rep (the user
the Task is *assigned to*), and team, with created/updated date filters and
cursor paging. The `team` filter dropped out of her refined list by
accident; Tomas confirmed 2026-09-09 it stays in scope.

## Requirements

**Endpoint**

- R1. New `GET /api/transcripts` on the API-key surface
  (`app/routes/api/`, `X-AIQ-API-KEY` middleware auth — free for any
  `/api/*` path, `app/main.py` jwt_middleware).
- R2. Strictly read-only: never triggers transcription on a miss, no
  Deepgram spend, no writes. Requesting transcription stays the job of
  `POST /api/transcription-jobs`.

**Filters** (all optional, ANDed; client's list except where annotated)

- R3. `createdAfter` / `createdBefore` — on Task `CreatedDate`
  (≈ call time; the value the sort uses, see R9). "After" filters are
  strict (`>`), "before" strict (`<`): a re-sync client saves the max
  timestamp it has seen and passes it back without re-fetching that row.
  Timestamps are ISO 8601; naive values are taken as UTC (the mirror
  stores naive UTC).
- R4. `callResult` — repeatable; same two-value enum as the POST
  (`appointment`, `pitch`). Expands server-side to the spelling-variant
  disposition lists via `CALL_DISPOSITION_MAP`
  (`app/schemas/transcription_job.py`); matches `sf_task.CallDisposition`.
- R5. `ownerId` / `ownerEmail` — the rep: Task `OwnerId` ("Assigned To" in
  the SF UI). `ownerEmail` resolves via `sf_user.Email` (indexed) to an id
  first; an unknown email is a 400, not an empty page — typos must surface.
  Supplying both is a 400.
- R6. `accountId` — `sf_task.AccountId`. IDs are 18-char SF ids (that is
  what the mirror stores); no 15-char normalization.
- R7. *(deferred — see Scope Boundaries)* `team` was in the client's
  original braindump but is out of scope for v1: it is blocked on the
  client defining whose team it means (rep's vs. account owner's — the
  existing POST filter means `Account.Owner.Partner_Sales_Team__c`, but
  her rep definition points at the Task assignee's team). Reserve the
  param name; return 400 on unknown params so its later addition is not
  silently ignored today.
- R8. `updatedAfter` — for re-syncing corrected (re-transcribed) records.
  Implemented against `sp_call_transcript.transcribed_at`, NOT
  `updated_at`: the Bedrock summary/highlights sweeper bumps `updated_at`
  without changing transcript content, which would feed the client
  phantom "corrections". `transcribed_at` moves exactly when transcript
  content changes. Each item returns `transcribedAt` so the contract is
  self-evident.

**Paging**

- R9. Keyset (seek) pagination; sort fixed at
  `(CreatedDate ASC, sfTaskId ASC)` — total and stable (`sfTaskId`
  unique), so a re-sync frontier only moves forward and pages never shift
  under concurrent auto-transcribe inserts. No direction knob.
- R10. `limit` default 100, max 500. Response carries `nextCursor`
  (opaque base64 of the last row's `{createdDate, sfTaskId}`) and
  `hasMore`. Clients pass `cursor` back verbatim, never construct one.
  Server-side seek predicate spelled out as
  `(CreatedDate > :d) OR (CreatedDate = :d AND sf_task_sf_id > :t)` —
  MySQL row-constructor comparisons optimize poorly.

**Response items**

- R11. Per item: `sfTaskId`, `accountId`, `ownerId`, `callDisposition`
  (raw SF value), `createdDate`, `transcribedAt`, `transcriptUrl`
  (presigned, 7-day TTL, re-minted per call — same rules as the existing
  GETs). Nothing speculative: no names, no JSON-transcript URL, no
  summary/highlights until asked for.

## Query shape (no migration)

Drive from `sp_call_transcript` (canonical, ≤1 row per Task — the only
rows worth returning), join `sf_task` on the unique `sf_id` for filters
and sort keys; `ownerEmail` resolves against `sf_user` first (one lookup,
no join in the main query). Every column already exists.

Sorting by joined `sf_task.CreatedDate` with keyset predicates is not a
pure index walk; at current volume (hundreds of transcripts/day) that is
fine. If it ever isn't, the fix is an index-only migration on `sf_task`
(`AccountId`, `OwnerId` have no usable standalone indexes today), not a
redesign. Do not add those indexes preemptively.

## Freshness caveat (goes in the endpoint docs)

Account/owner/disposition come from the `sf_task` mirror. Tasks are in the
hourly `sfdc-stream` sync (added by the auto-transcribe PR #712 —
`app/commands/sfdc/stream.py`), so a transcript can exist for up to ~1h
before the search can see it. Acceptable for retrieval; documented so it
is not filed as a bug.

## Scope Boundaries

- **`team` filter deferred**, not dropped: blocked on the client
  confirming whose team (rep's, i.e. `sf_user.Partner_Sales_Team__c` of
  `Task.OwnerId` — the lean, since she defines rep as the assignee — vs.
  the account owner's, which is what the POST's existing `team` filter
  means). Adding it later is a query-time join on already-indexed
  columns; no contract or schema change.
- No transcribe-on-miss, ever.
- No offset paging, no free-text search, no sort options.
- Existing job endpoints unchanged; this does not replace them.
- No new columns, no denormalized team/account/owner on transcript rows.

## Dependencies / Assumptions

- `sp_call_transcript` populated for every completed transcription
  (`_ensure_call_transcript`, see `NOTES.md` pipeline diagram) — this
  endpoint inherits any gap where that non-fatal step failed.
- `sf_user.Email` and `sf_user.Partner_Sales_Team__c` indexed (verified
  2026-09-09, `app/models/sf_user.py`).
- Hourly stream sync includes `task` (verified 2026-09-09 against
  `stream.py` + git: added in #712).
