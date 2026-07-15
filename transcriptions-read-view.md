---
date: 2026-07-08
topic: transcriptions-read-view
---

# Transcriptions Read View (udab-client)

## Problem Frame

The bulk call-transcription feature (udab-server `8ae2bba`, PR #643) lets the client's team fire transcription jobs against Salesforce Tasks, with DeepGram doing the transcription and transcripts landing on S3. There is currently no way to see what's been requested and produced without querying the DB.

Primary purpose: **usage monitoring / lightweight telemetry**. DeepGram costs money — we want to see at a glance how many requests were made, what parameters people used, and how many transcriptions were produced. Read-only: the "R" in CRUD, nothing else.

Calls and Transcriptions are close cousins (both map to SF Tasks; Calls = live transcriptions, Transcriptions = ex-post batch transcriptions) and may merge later — so the new view lives beside the existing Calls page and reuses its patterns.

## Requirements

**Navigation**

- R1. The existing Calls page gains two tabs: **"Calls"** (current page content, unchanged) and **"Transcriptions"** (new view). Same top-level "Calls" menu entry, same `VIEW_CALLS` permission. No new sidebar item.

**List view (Transcriptions tab)**

- R2. Tabular list of transcription requests (jobs), one row per job, newest first. Default state: no date filter, first page of 20 loaded on open.
- R3. Columns per row:
  - Created timestamp (when the request was fired)
  - Call result (e.g. Meeting Set)
  - Queried SF date window (`start_date` – `end_date`)
  - Parameters used, rendered compactly (badges/chips) from `filters_json`: team, account owner, account, contact — the "who asked for what" telemetry signal
  - Job status (pending / processing / completed / failed)
  - Counts: matched → transcribed / skipped / failed (e.g. "50 → 42 / 5 / 3")
- R4. Failed jobs surface their job-level error message in the list (tooltip or inline).
- R5. Date-range filter on **request created date** (not the SF appointment window), using the same date-picker widget as the Calls tab. Paging via the shared `Pagination` component.
- R6. A summary strip above the table aggregates the currently filtered set: "N requests → M transcriptions". With no filter applied it shows all-time totals (doubles as a DeepGram spend ballpark).

**Detail view**

- R7. Clicking a **completed** job opens its detail (modal, consistent with the Calls tab's detail pattern). The detail action is disabled for jobs in any other status — we know the status, no need to fetch details for in-flight or failed jobs.
- R8. Detail shows the job's task list, one row per SF Task:
  - Link to the Task in Salesforce (`https://amg.lightning.force.com/lightning/r/Task/{sf_task_sf_id}/view` — established client pattern)
  - Task status; skip reason or error message where present
  - **View transcript** action where a transcript exists
- R9. Viewing a transcript reuses the transcript viewer from the Calls detail modal (speaker-parsed text rendering), fed by the presigned S3 URL. The viewer should be extracted into a shared component rather than duplicated.

**Server (new read endpoint)**

- R10. New `GET /transcription-jobs` list endpoint (JWT + `VIEW_CALLS`, like `/call-transcripts`): paged, newest first, optional created-date range filter. Returns per job: id, created_at, call_result, start/end date window, filters, status, error_message, and derived per-status task counts (same GROUP BY approach the by-id endpoint uses). Also returns the aggregate totals for the summary strip (R6).
- R11. New `GET /transcription-jobs/{id}/tasks` detail endpoint (same auth) returning the task list with fresh presigned transcript URLs. *(Implementation note: the original plan to reuse `GET /api/transcription-jobs/{id}` didn't survive contact with the auth model — `/api/*` routes use API-key auth for the call-quality team, while udab-client authenticates with JWT, so the read view gets its own thin JWT-facing endpoints.)*

## Success Criteria

- Opening Calls → Transcriptions answers "how many requests today/this week, with what parameters, producing how many transcriptions" without touching the DB.
- Any completed transcript is reachable and readable in two clicks (row → View transcript).
- The Calls tab behaves exactly as before.

## Scope Boundaries

- Read-only. No creating, retrying, or deleting jobs from this view.
- **No DB migration** — work with data already captured. Contact/Account names are not stored on task rows and are NOT looked up (neither denormalized at creation nor fetched live from SF); the SF Task link is the path to that context.
- No detail view for pending/processing/failed jobs (v1).
- Date-range is the only filter for now; more filters (team, call result, …) later.
- No merge of Calls and Transcriptions views yet — just tabs side by side.

## Key Decisions

- **Row grain = transcription request (job)**, with per-task drill-down in the detail — matches the telemetry purpose. (A flat per-task list was considered; better for an eventual Calls merge but worse for usage monitoring.)
- **Summary follows the filter**; unfiltered = all-time. One mental model, and the all-time number is the cost-relevant one.
- **Transcript viewing in-app** via the existing Calls transcript viewer rather than a bare download link — the component exists, reuse is cheap, and it keeps the two tabs converging rather than diverging.
- **Detail restricted to completed jobs** — matches the current API contract (tasks are only returned once completed); avoids server contortions for in-flight jobs.

## Dependencies / Assumptions

- Server: `sp_transcription_job` / `sp_transcription_job_task` tables and both existing endpoints from PR #643 (`app/routes/api/transcription_job_api.py`).
- Client reference implementation: `src/pages/calls/CallsPage.vue` (date-range filter, table, `Pagination`, detail modal with transcript viewer).
- Presigned transcript URLs are minted fresh on each detail fetch (7-day TTL) — no URL staleness concern for this view.
- SF instance URL is hardcoded as `amg.lightning.force.com` across the client; this view follows the same convention.

## Resolved During Implementation

- Tab mechanics: in-page `activeTab` state with bootstrap nav-tabs — the established client convention (VisitorResolutionPixel, UploadDetail); no sub-routes.
- Transcript viewer seam: `src/components/calls/TranscriptViewer.vue` takes a presigned `url` prop and owns fetch + speaker-parsed rendering; both tabs use it.
- Summary totals ride on the list response (`summary: {jobs, transcribed}`) — no separate endpoint.

## Status

Implemented (2026-07-08): udab-server `app/routes/transcription_job.py` + schemas + tests; udab-client `CallsPage` (tabs) / `CallsTab` / `TranscriptionsTab` / `TranscriptViewer`. Deploy server first, client second.
