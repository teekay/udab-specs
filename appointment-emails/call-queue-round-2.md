---
kind: spec
status: ready
area: appointment-emails
updated: 2026-09-11
repos: [udab-server, udab-client]
summary: "Call queue round 2: pitch calls join the population and Kind filter; per-call and bulk (ZIP) transcript export."
---

# Call Queue round 2 — pitches in the population, transcript export

Status: READY 2026-09-11. Client ask 2026-09-09; answers relayed by
Tomas 2026-09-11; Q1 (filter shape) settled by Tomas the same day —
no questions open. Builds directly on
[call-queue.md](call-queue.md) (shipped to prod; see its Implemented
sections for the code map).

Client ask (2026-09-09, verbatim):

> we could add pitches to this as well (and just add that to the
> filter and also a way to download an individual transcript OR select
> and bulk export a group of transcripts from the view. (with the
> other headers in the export as well).

## Decided (client answers, 2026-09-11)

- **Pitch = the existing pitch bucket.** The same 4-variant list the
  transcription handover confirmed 2026-08-24
  (`CALL_DISPOSITION_MAP[CallResult.pitch]`: "KDM Pitched" + three
  "Pitch Follow-Up" spacings). No new variants. This reverses round
  1's Q1 note ("Pitches are assumed OUT").
- **Bulk export = a ZIP carrying one file per transcript**, plain
  text over JSON — the client expects manual processing. "The other
  headers" (the table's columns) ride along in the export.
- **Export covers checked rows only.** No export-everything-matching-
  the-filter mode.
- **Untranscribed rows are skipped.** The UI already shows which rows
  have no transcript, so there is nothing to produce for them. A
  request whose selection contains *zero* transcribed calls returns a
  no-content response and the frontend shows a notification explaining
  there is nothing to download.
- **Naming stays "Appointment Calls"** — page, nav, permission — for
  now. More feedback is coming; do not pre-rename anything.

## Decided (Tomas, 2026-09-11)

- **Q1. Pitches get two Kind values**, in the existing dropdown:
  **Pitch** ("KDM Pitched") and **Pitch follow-up** (the "Pitch
  Follow-Up" spellings) — mirroring how appointment follow-ups are
  already split out. Rationale: collapsing two kinds into one later
  is cheap; splitting one into two later invalidates saved filter
  preferences.
- **No substring matching in queries.** The current `KIND_EXPR`
  classifies dispositions with `LIKE '%confirm%'`-style predicates;
  data volume is substantial, so kinds are instead derived at import
  time from the fixed disposition lists and filtered with plain `IN`
  (see Design). Kills the round-1 lean's LIKE-based SQL twin.

## Leans (Tomas, settle at implementation)

- *(lean)* **One export format for both paths.** The individual
  download and each ZIP member are the same `.txt` file: a metadata
  header block (the table's columns + the SF Task link), a separator,
  then the transcript. No separate manifest CSV in the ZIP — the
  headers live in each file, which survives files being copied out of
  the ZIP individually. Add a manifest only if the client asks.
- *(lean)* **Selection is a Set of `sf_task_sf_id`s** that survives
  paging (check on page 1, page over, check more) and clears when
  filters change (the checked rows may no longer match). Header
  checkbox selects/deselects the current page. Cap 200 selected
  (matches `per_page` max; keeps the ids-in-query-string request
  ~4 KB, under URL limits).
- *(lean)* **All rows are checkable**, including untranscribed ones —
  keeps select-all trivial; the server skips what it can't export.
  Only the all-skipped case surfaces (no-content → toast).
- *(lean)* **Individual download lives in the table row**: a download
  icon in the Transcript column, shown only on `transcribed` rows.
  (The ask says "from the view".) No flyout button unless asked.

## Design

### Population and kinds (udab-server)

All in `app/services/appointment_calls.py`; the route validates
against the same constants.

- `population_filters()`: disposition filter widens from
  `APPOINTMENT_DISPOSITIONS` to appointment + pitch buckets.
- **New kinds `pitch` and `pitch_follow_up`**, appended to `KINDS`
  (drives filter validation and `filter_options()`).
- **Kind becomes a compile-time map, and LIKE leaves the SQL.** The
  population is a fixed, finite list of disposition strings
  (`CALL_DISPOSITION_MAP`, both buckets), so classify each string
  **once at import time** into a `disposition (lowercased) → kind`
  map plus its reverse (`kind → [disposition variants]`). Rules for
  building the map: pitch-bucket strings first ("Pitch Follow-Up" →
  `pitch_follow_up`, "KDM Pitched" → `pitch` — bucket-first matters
  because the string contains "follow"), then the existing
  confirm/follow/resched/booking chain for the appointment bucket.
  One map is the single source of truth for Python and SQL:
  - `call_kind(disposition)` = lookup on the stripped/lowercased
    string; a miss falls back to `booking` (unreachable for
    population rows, modulo collation pad-space edge cases).
  - The `kinds` filter becomes
    `SfTask.CallDisposition.IN(<variants of the selected kinds>)` —
    the same predicate family as the population filter, served by
    the existing `ix_sf_task_disposition_created (CallDisposition,
    CreatedDate)` index (migration `de95c1fbb4eb`). No `LIKE`
    anywhere in the access path.
  - The `kind` **sort** is a `CASE` mapping each kind's `IN`-list to
    its rank — evaluated only to order the already-filtered set,
    never to find rows.
  - The current `KIND_EXPR` LIKE-CASE and the substring body of
    `call_kind()` are deleted, not kept as fallbacks.
- Nothing else moves: pitch calls have been transcribed by the
  `auto-transcribe` poller since the same 2026-08-25 go-live
  (transcription/auto.md), so rows appear with transcripts,
  summaries and highlights (generic call framing, `context_kind =
  "call"`, PR #735) immediately — no backfill, none possible (vendor
  audio for older calls has expired). Pitch rows have no appointment
  email; the flyout's briefing link is already conditional and the
  Briefing column renders its existing em-dash.
- Volume: the auto spec's ~1,200 calls/workday figure is appointment
  **and** pitch combined, so the queue grows to roughly that; the
  pitch share has not been measured (unverified).

### Export endpoints (udab-server)

Both are pure reads — no side effects, no spend — behind the existing
`VIEW_APPOINTMENT_CALLS` permission, in
`app/routes/appointment_calls.py`.

- `GET /appointment-calls/{sf_task_sf_id}/transcript/download` —
  `text/plain` attachment (`Content-Disposition` names the file),
  header block + transcript. 404 when the Task is outside the
  population or not transcribed, same contract as the existing
  `/transcript` JSON route (which stays — the flyout uses it).
- `GET /appointment-calls/export?ids=<csv of sf_task_sf_id>` —
  `application/zip` (StreamingResponse). 422 on empty or >200 ids.
  Ids outside the population and rows without a transcript are
  silently skipped (the population is authoritative); **zero
  exportable rows → 204 No Content** (the client-side contract below
  depends on this).
- Implementation: do **not** loop `transcript_text()` — it re-runs
  the full joined query per call. One `_base_select()` filtered to
  the ids fetches metadata + transcript keys for the whole selection;
  key resolution per row matches `transcript_text()` (newest job-task
  key, else the conventional key). S3 reads via
  `asyncio.to_thread(read_s3_file, …)` under a semaphore (~8
  concurrent). ZIP built in memory — 200 transcripts at tens of KB
  each is a few MB, no async job needed.
- Naming: ZIP `appointment-call-transcripts_<YYYY-MM-DD>.zip`;
  members `<YYYY-MM-DD_HHMM>_<account-slug>_<sf_task_sf_id>.txt` —
  the id suffix guarantees uniqueness.

Header block (fields = the table's columns minus transcript state,
plus the SF Task link; `Called` is the SF `CreatedDate`, UTC):

```
Account:   Acme Roofing
Contact:   Jane Doe — Office Manager
Rep:       John Smith
Team:      Team 3
Industry:  Construction
Kind:      Pitch follow-up
Called:    2026-09-10 14:32 UTC
Duration:  6:41
SF Task:   https://amg.lightning.force.com/lightning/r/Task/00T…/view
----------------------------------------------------------------------
<transcript>
```

Missing values render as `—`, lines never dropped, so the block is
positionally stable for the client's manual processing.

### Client (udab-client)

- `src/constants/appointment-calls.js`: two new `KINDS` entries
  (labels "Pitch" / "Pitch follow-up", distinct badge classes). The
  filter bar and the `sanitizeViewPreferences` whitelist both derive
  from `KINDS`, so they pick the change up for free; previously
  stored view-preferences stay valid.
- `AppointmentCallsPage.vue`: leading checkbox column + header
  select-all (current page), selection Set as per the lean, an
  "Export selected (n)" toolbar button (disabled at 0), row download
  icon on transcribed rows. Both downloads go through
  `api.download()`.
- **`api.download()` must learn about 204.** Today it unconditionally
  saves the blob — a 204 would save an empty file. Change: on status
  204 skip `saveBlob` and return `false` (existing callers ignore the
  return value, so this is backward compatible); the page toasts
  "None of the selected calls have a transcript." on `false`.

## Out of scope

- Export-by-current-filter, select-all-across-pages beyond the cap.
- New pitch disposition variants (the bucket is a fixed code
  constant; new spellings wait for a deploy, as with appointments).
- Async/background export jobs, manifest CSV, per-file formats other
  than TXT.
- Any renaming of the page, nav item, or permission.

## Implementation plan

Server first (the client change is inert without the wider
population). Branch `appointment-calls-feedback-round-1` already
exists in udab-server, cut from master; PR targets
`abstrakt-mg/udab-server`. udab-client's local `master` is behind
`upstream/master` (the round-1 UI merged as PR #308) — update before
branching.

1. **udab-server** — population + kinds (the disposition→kind map
   replacing `call_kind`'s substring body and the `KIND_EXPR`
   LIKE-CASE), then the two export routes and the batched export
   service function. Tests: the map covers every disposition
   constant in both buckets (and "Pitch Follow-Up" does *not* land
   in appointment `follow_up`), the kinds filter compiles to a
   disposition `IN` (no LIKE), population widening, export happy
   path / skip-untranscribed / all-skipped 204 / cap 422 /
   permission, single-download 404s.
2. **udab-client** — constants + badges, selection UI, export and
   row-download buttons, `api.download` 204 handling. Vitest:
   constants (kind label/badge/sanitize), selection semantics
   (paging survival, filter-change clear, cap), export button state,
   204 → toast path.
3. Ownership boundary from round 1 holds: everything here is
   read-view code (Tomas's); no summary/highlights/appointment-email
   surface (Dani's) is touched.
