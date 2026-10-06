---
kind: spec
status: done
area: appointment-emails
updated: 2026-10-06
repos: [udab-server, udab-client]
summary: "Download all: Batch job zips every transcribed call matching the queue filters to S3; pre-signed URL, no table."
---

# Call Queue — "Download all" (filter-driven bulk transcript export)

Status: DONE — shipped as udab-server d987f75, udab-client cb937b1 (status corrected 2026-10-06). Original line: design agreed with Tomas (no table,
same ZIP format, soft ceiling, pre-signed URL + localStorage, dedicated
permission, shared export pipeline) and **built the same day in both
repos on branch `btn-download-all-transcripts`, uncommitted, awaiting
Tomas's review** (see Implemented). Builds on the round-2
export ([call-queue-round-2.md](call-queue-round-2.md), checked-rows
ZIP, ≤ 200 ids) and the round-3 Appointments / Pitches views
([NOTES.md](NOTES.md)). Read NOTES.md first for how the queue works.

Ask (Tomas, 2026-09-22):

> I need a new button "Download all" that would download all
> transcripts matching the entered criteria. […] since we want to
> remove the ceiling (or set it to a really high value like 10,000),
> I'm thinking we may have to split this up, create a new background
> job, kick it off, take its ID, then query periodically in the
> frontend until the ZIP is available.

Who it is for: the CEO / CTO, who want to grab everything and feed
it to an LLM. One-click-and-wait is the whole UX; nothing else on the
page changes.

## Decided (Tomas + Claude, 2026-09-22)

- **Background job, not a bigger synchronous request.** Reasons in
  Analysis §2. The existing checked-rows export (≤ 200 ids,
  synchronous, in-memory) stays exactly as it is.
- **No table, no history.** The server keeps nothing about an
  export. The `POST` mints an id and a pre-signed download URL up
  front and hands both to the browser; the browser keeps them in
  `localStorage`. Progress and errors live in a tiny status object
  next to the ZIP in S3 (Design).
- **ZIP format is the same as today, and so is the code** (Tomas,
  2026-09-22: no duplication). The selected-rows route and the job
  are two entry points into one export pipeline in the service —
  same loader, same archive writer, same member naming. The round-2
  in-memory ZIP builder is refactored onto that pipeline rather than
  copied (Design, "One export pipeline, two entry points").
- **Soft ceiling, not a hard one.** The page already knows the
  list's `total` for the current filters; above **10,000** the button
  asks "Are you sure? (up to N calls)". No hard cap: the Batch
  attempt timeout bounds the job, and "everything" is a legitimate
  request.
- **7-day retention** of the ZIPs (S3 lifecycle rule on the prefix);
  the pre-signed URL expires at the same time.
- **Untranscribed rows are skipped** — same rule as round 2.
- **A dedicated permission gates the button and its endpoints**
  (Tomas, 2026-09-22): `Download All Appointment Calls`. Seeing the
  page (`Appointment Calls`) does not imply it; the selected-rows
  export stays under the page permission. Same mechanism as every
  other permission: a string constant on both sides, carried in the
  JWT, assigned per role in the role editor — no migration.

## Open questions (Tomas)

- **Q1. Keep the status sidecar?** *(lean: yes)* It is ~10 lines in
  the job and the only way the page learns "1,500 / 18,342" or
  "failed: …" without a table. Cutting it leaves: `HEAD` on the ZIP
  key for readiness, and a client-side give-up timer for failures.

## Deferred — Batch queue contention (Tomas, 2026-09-22)

The export runs on the shared `main` Batch queue with
`transcribe-calls-job`, landing-page operations and the
appointment-email pipeline. Queues are FIFO, so a whole-population
export (minutes now, ~20 min in a year) waits behind whatever is
queued and, once running, holds a slot in front of it. The queue is
busiest at night; the CEO clicks during the day.

**MVP decision: ship as is, tell the client "if you ever wait too
long, say so".** Two remedies, both analysed, neither built:

- **Dedicated queue (the right fix).** Isolation comes from *where*
  a job is submitted, not from its size: one more entry in
  `launch_job()`'s queue map plus a queue and (Fargate, idle-free)
  compute environment in AWS. Takes exports of any size out of the
  nightly pile-up; search-export would benefit from the same queue.
- **Size cutoff with a synchronous path (rejected as the primary
  lever).** Technically sound and cheap thanks to the shared
  pipeline (`run_export()` into a `BytesIO`, ~40 lines + a client
  branch on `total`): a conservative cutoff is ~1,000 transcripts
  (~10 s, ~10 MB, load-balancer idle timeout is the wall at ~60 s).
  But it only rescues the narrow pulls; "everything" (~18k today)
  and "a month of pitches" (~15k) still queue, and it puts export
  work back on the API pod the sales floor uses.

Trigger to revisit: the client reports a long "Queued…" wait. Then
do the dedicated queue first; add the cutoff only for snappiness.

## Leans (settle at implementation)

- *(lean)* **Filters travel as one JSON CLI argument** to the job
  (`--filters='{…}'`), exactly the list's query params as received.
  `launch_job()` passes a command list (no shell), so quoting is a
  non-issue; a hundred account ids is ~2 KB, well within limits. The
  pre-signed URL is *not* passed to the job — it does not need it,
  and Batch command overrides are visible in the console.
- *(lean)* **The download is a user click, not an auto-open.**
  `window.open` from a timer callback gets popup-blocked; the ready
  state renders a button.
- *(lean)* **One in-flight export per browser.** The button is
  disabled while `localStorage` holds a non-terminal export. No
  server-side guard (there is nothing server-side to guard with);
  two tabs can start two jobs, which is harmless.
- *(lean)* **Job reads through the reader session**
  (`ReadonlyAsyncSessionLocal`, as search-export does).
- *(lean)* **Streaming ZIP on the job's disk**, then one
  `upload_local_file_to_s3()`. Never the whole archive in memory —
  the population is tens of thousands of rows now, hundreds of
  thousands within a year.

## Analysis

### 1. Volume — why the 200 cap cannot just be raised

Prod profile (call-queue.md §0, NOTES.md "Local testing"): the
population grows ~1,200 rows per workday (appointments + pitches),
since go-live 2026-08-25. Today that is ~20k rows and ~15–18k
transcripts; in a year, ~300k rows. Real transcripts run ~5–10 KB
(diarized, ~5-minute calls; pitches shorter) — the seed's ~1 KB
objects are not representative. *(Estimates; replace with a prod
count + average object size when available.)*

| Selection | Files | Raw | ZIP (≈ 4:1) | S3 GETs @ 16 conc. |
|---|---|---|---|---|
| Today, no filter | ~18k | ~120 MB | ~30 MB | ~1–2 min |
| One month, pitches | ~15k | ~90 MB | ~25 MB | ~1–2 min |
| One rep, one month | ~1k | ~7 MB | ~2 MB | ~5 s |
| In a year, no filter | ~250k | ~1.7 GB | ~400 MB | ~15–25 min |

Ids-in-query-string dies first (18k × 19 chars ≈ 350 KB URL), then
the in-memory ZIP, then the request duration. A ceiling of 10,000
would already be below "everything" today.

### 2. Why not a synchronous streaming response

A `StreamingResponse` that zips on the fly is the least code, and it
was considered:

- **Duration.** Minutes per request, held open through the load
  balancer and the browser. Any stall over the idle timeout (S3
  hiccup, one slow chunk) drops the connection and the user starts
  over with nothing.
- **Browser memory.** `api.download()` is an axios blob: the whole
  archive is buffered in the tab before `saveBlob`. Hundreds of MB
  in a tab is fragile.
- **API process.** The reads run on the API's thread pool for the
  whole request; two clicks = two multi-minute jobs inside the
  request-serving process. The Batch container is the right place
  for this, and `launch_job()` already exists.
- **Nothing to come back to.** No retry, no re-download, no
  "it finished while you were away".

### 3. Why pre-signing before the object exists works

A pre-signed URL is a signature over the *request* (bucket, key,
expiry, response headers), not over the object. Minting it for a key
that does not exist yet is valid; the URL simply returns an error
until the upload lands and then serves the file. The `POST` can
therefore return the final download URL immediately, with the ZIP
filename baked in via `ResponseContentDisposition`, and the server
never needs to remember anything.

Constraint: a 7-day expiry (SigV4's maximum) needs long-lived access
keys — under temporary role credentials the URL dies when they
rotate. Audio playback already hands out 7-day URLs
(`AUDIO_PRESIGN_TTL`) and works in prod, so this holds today; verify
at PR time.

### 4. What already exists to copy

- `app/services/aws.py` — `launch_job()` (Batch, or a local
  subprocess when `RUN_JOBS_LOCAL=1`, which the local `.env` sets),
  `generate_presigned_get_url()` / `_get_s3_presign_client()` (uses
  `S3_PUBLIC_ENDPOINT_URL` locally so the browser can reach MinIO),
  `upload_local_file_to_s3()`, `read_s3_file()`, `write_s3_file()`.
- `app/services/appointment_calls.py` — `ListFilters.from_query()`,
  `_base_select()`, `_transcript_state_expr()`, `load_transcripts()`
  (metadata + key resolution + parallel S3 reads for a batch of ids),
  `ExportedTranscript.filename/document`, `export_zip_name()`.
- `app/commands/search_export.py` — the shape of a Batch command that
  writes to `s3://abstrakt-intelligence/exports/…`.
- `udab-client/src/pages/email-validation/EmailValidationPage.vue` —
  `setInterval` polling while something is in flight.

## Design

### S3 layout

Bucket `abstrakt-intelligence`, prefix `exports/appointment-calls/`:

- `<uuid>.zip` — the archive.
- `<uuid>.status.json` — written only by the job:
  `{"state": "processing" | "complete" | "error", "total": int|null,
  "exported": int, "size_bytes": int|null, "error": str|null}`.

Lifecycle rule: expire the prefix after 7 days (ops, at PR time).

### udab-server

**Permission**: `EXPORT_ALL_APPOINTMENT_CALLS = 'Download All
Appointment Calls'` in `app/constants/permission.py`, next to
`VIEW_APPOINTMENT_CALLS`.

**Endpoints** (`app/routes/appointment_calls.py`, both behind
`require_permission(EXPORT_ALL_APPOINTMENT_CALLS)` — not the page
permission — registered before `/{sf_task_sf_id}`):

- `POST /appointment-calls/exports` — body: the list's filter query
  params (same names, same csv strings; `page`/`per_page`/`sort`/`dir`
  ignored). Validates with `ListFilters.from_query()` (422 on a bad
  kind/date, so the job never fails on input). Then:
  `export_id = uuid4().hex`; `download_url =
  generate_presigned_get_url(bucket, f"{prefix}/{export_id}.zip",
  expires_in=7 d)` with `ResponseContentDisposition:
  attachment; filename="appointment-call-transcripts_<YYYY-MM-DD>.zip"`
  (extend the helper to accept response headers, as
  `transcription_jobs.py:654` already does for transcripts);
  `launch_job(db, command="appointment-calls-export",
  args={"export-id": export_id, "filters": json.dumps(params)},
  queue="main", timeout_seconds=7200)`. `launch_job` → `None` ⇒ 502.
  Response `{export_id, download_url, expires_at}`.
- `GET /appointment-calls/exports/{export_id}` — `export_id` must be
  32 hex chars (422 otherwise). Reads `<export_id>.status.json`; a
  missing object ⇒ `{"state": "queued"}`; else the JSON as is. One
  small S3 GET, no side effects — read-only.

Response schemas in `app/schemas/appointment_calls.py`.

**One export pipeline, two entry points** (all in
`app/services/appointment_calls.py`; the route and the command are
thin callers, and the tests target the service):

```
ids ──► iter_exports(db, ids) ──► ExportArchive.add(export) ──► bytes / file
 ▲                                      ▲
 │ route: csv of ids (≤ 200)            │ route: BytesIO, one batch
 │ job:   transcribed_ids(db, filters)  │ job:   temp file, per chunk + status
```

Refactor of the existing round-2 code (behaviour-preserving, same
tests must pass unchanged):

- `ExportArchive` — context manager over any binary file object
  (`BytesIO` or an open temp file) wrapping `zipfile.ZipFile(…, "w",
  ZIP_DEFLATED)`, with `add(export: ExportedTranscript)` and a
  `count`. Replaces the loop inside `build_export_zip()`;
  `build_export_zip(exports) -> bytes` stays as the three-line
  convenience the route already calls, now implemented on top of it.
  There is exactly one place that knows how a member is named and
  written.
- `iter_exports(db, ids, chunk_size=500)` — async generator yielding
  `ExportedTranscript`s in call order, `load_transcripts(db, chunk)`
  per chunk (unchanged: one metadata query, key resolution, parallel
  S3 reads under `EXPORT_S3_CONCURRENCY`, untranscribed skipped).
  `load_transcripts()` keeps its signature; the route may keep
  calling it directly for its single ≤ 200 batch, or move to
  `iter_exports` — either way no second loader.
- `transcribed_ids(db, filters: ListFilters) -> list[str]` — new:
  `_base_select().with_only_columns(SfTask.sf_id).where(
  *filters.criteria(), _transcript_state_expr() == STATE_TRANSCRIBED)
  .order_by(SfTask.CreatedDate, SfTask.sf_id)`. The only query the
  job adds; it reuses the list's predicate builder verbatim.
- `export_header()`, `ExportedTranscript.filename/document`,
  `export_zip_name()`, `KIND_LABELS` — untouched, shared as before.
- `run_export(db, filters, target_file, on_progress)` — the job's
  body, in the service: `ids = transcribed_ids(…)`, then
  `ExportArchive(target_file)` fed from `iter_exports(db, ids)`,
  calling `on_progress(exported, total)` after each chunk. Returns
  `(total, exported)`. The command supplies the temp file and an
  `on_progress` that writes the status object; the tests call
  `run_export` with a `BytesIO` and a recording callback and never
  touch Batch or the CLI.

**Command** `appointment-calls-export --export-id <hex> --filters
<json>` (`app/commands/appointment_calls_export.py`, registered in
`app/commands/__init__.py`) — orchestration only:

1. Write status `processing` (total null, exported 0).
2. `filters = ListFilters.from_query(**json.loads(filters))`.
3. `total, exported = await run_export(db, filters, tmp_file,
   on_progress=write_status)` — the first `on_progress` call carries
   `total` (write status), each chunk bumps `exported` (write
   status). Rows that lose their transcript mid-run are simply
   absent; `exported` may end below `total`.
4. `total == 0` ⇒ status `complete` with `size_bytes: 0` and no ZIP
   upload. Otherwise `upload_local_file_to_s3(bucket, key, tmp_path)`
   then status `complete` with `size_bytes` — status is written
   **after** the upload so `complete` always means downloadable.
5. Any exception ⇒ status `error` with the message, re-raise so
   Batch records the failure.

`EXPORT_S3_CONCURRENCY`: raise from 8 to 16 for both paths, measure
on the volume seed.

### udab-client

- `src/helpers/udab-api.js`: `requestAppointmentCallExport(params)`
  (POST), `getAppointmentCallExport(id, { showLoader: false })`.
- `src/constants/permissions.js`: `EXPORT_ALL_APPOINTMENT_CALLS =
  'Download All Appointment Calls'`, added to
  `APPOINTMENTS_PERMISSIONS` so the role editor lists it under
  Appointments.
- `src/constants/appointment-calls.js`: `EXPORT_ALL_CONFIRM_ABOVE =
  10000`; `EXPORT_STORAGE_KEY = 'appointment-calls-export'`.
- `AppointmentCallsPage.vue` toolbar, next to "Export selected (n)":
  **"Download all"**, rendered only when
  `hasPermission(EXPORT_ALL_APPOINTMENT_CALLS)` (`@/helpers/auth`);
  the export pill and the on-mount storage resume are behind the
  same check, so a user who lost the permission sees nothing and
  polls nothing. Click → if `total > EXPORT_ALL_CONFIRM_ABOVE`,
  confirm (Vue `v-if` modal, no Bootstrap JS: "Prepare a ZIP with up
  to N transcripts? This runs in the background.") → POST with
  `buildListParams(...)` minus paging/sort → save
  `{export_id, download_url, expires_at, requested_at, state}` to
  `localStorage` → poll every 3 s (`setInterval`, cleared on unmount
  and on a terminal state).
- **Export pill** replaces the button while an export exists in
  storage: `queued` → "Queued…"; `processing` → "Preparing
  {exported} / {total}" (total null → "Preparing…"); `complete` with
  `size_bytes > 0` → "Download ready ({size})" button (click =
  `window.open(download_url)`) + "Dismiss"; `complete` with 0 →
  toast "No transcribed calls match these filters", storage cleared;
  `error` → toast with the message, storage cleared; `expires_at`
  passed → storage cleared silently.
- On mount: read storage; if present and unexpired, show the pill
  and resume polling unless already `complete`. Survives F5 and
  closing the tab; per browser only (accepted).
- Changing filters or view while an export runs does not cancel it
  (the job already captured its filters); the pill stays.
- Tests (vitest): button, pill and storage resume absent without the
  permission, present with it; POST payload equals the list's filter
  params; confirm only above the threshold; polling transitions; ready click
  opens the url; storage round-trip on mount; empty / error /
  expired handling.

### Tests (udab-server, `tests/test_appointment_calls.py`)

- Both routes 403 for a user who holds `Appointment Calls` but not
  `Download All Appointment Calls`.
- `POST`: 422 on bad filters; returns a 32-hex id and a URL whose
  key is `exports/appointment-calls/<id>.zip` with the
  `response-content-disposition` query parameter; `launch_job` is
  called with the id and the filters JSON (patched).
- `GET`: 422 on a malformed id; `queued` when the status object is
  missing; passes the JSON through otherwise (MinIO / stub).
- Refactor guard: the existing round-2 export tests
  (`test_204_when_nothing_is_exportable`, member naming, header
  block, skipped ids) pass unchanged against the `ExportArchive` /
  `iter_exports` implementation.
- `transcribed_ids()`: honours every `ListFilters` criterion and
  excludes non-transcribed rows, in call order.
- `run_export()` with a `BytesIO` and a recording `on_progress`
  against the fixture population + MinIO stub: one member per
  transcribed matching row and none for untranscribed / non-matching
  rows; `on_progress` called with `total` first, then once per
  chunk; `total == 0` yields an empty archive and `(0, 0)`; an S3
  read failure propagates.
- Parity: the archive `run_export()` builds for filters matching ids
  `{a, b}` is member-for-member byte-identical to
  `GET /export?ids=a,b`.
- Command: status object transitions `processing` → `complete` with
  `size_bytes` (written after the upload); `total == 0` → `complete`,
  no ZIP; an exception → `error` with the message.

## Out of scope

- Cancelling a running export.
- Server-side history, list endpoint, notification, email.
- Any change to the selected-rows export, its 200 cap, or the
  checkbox UX.
- Audio in the archive.

## Implementation plan

1. udab-server, first commit: the refactor alone (`ExportArchive`,
   `iter_exports`, `build_export_zip` on top of them) with the
   round-2 tests green and no behaviour change. Second commit:
   `transcribed_ids`, `run_export`, presign helper gains response
   headers, `POST`/`GET` routes + schemas, command,
   `EXPORT_S3_CONCURRENCY` bump, tests.
   Verify locally with `RUN_JOBS_LOCAL=1` against the volume seed
   (~10k transcripts in MinIO): time the job, open the URL in a
   browser (needs `S3_PUBLIC_ENDPOINT_URL`).
2. udab-client: api helpers, button + threshold confirm + pill +
   polling + storage, tests.
3. Ops at PR time: assign `Download All Appointment Calls` to the
   CEO / CTO role(s) only (role editor; the page permission stays
   as is); S3 lifecycle rule (7 d) on
   `exports/appointment-calls/`; confirm the API's S3 credentials are
   long-lived keys (7-day presign); confirm the `main` Batch job
   definition's memory (the job holds one 500-id chunk, not the
   archive); note Batch cold start (~1–2 min) as the expected
   "Queued…" duration.
4. Update NOTES.md "Export" once merged.

## Implemented (2026-09-22)

Both repos on branch `btn-download-all-transcripts`, working tree only
(not committed — Tomas reviews first). Deviations from the Design above
are called out inline.

### udab-server

- `app/services/appointment_calls.py`
  - Refactor: `ExportArchive` (context manager over any binary file
    object, `add(export)`, `count`); `build_export_zip()` is now three
    lines on top of it. Round-2 tests pass unchanged.
  - `iter_export_batches(db, ids, chunk_size=500)` — *deviation:*
    yields one **list per chunk** rather than single exports, so a
    caller sees chunk boundaries for progress. `load_transcripts()`
    untouched.
  - `transcribed_ids(db, filters)`, `run_export(db, filters, target,
    on_progress)` (`on_progress` is an **async** callable, awaited
    with `(0, total)` first and `(exported, total)` after each chunk).
  - **No read view spans the job** (added 2026-09-22 after the
    risk review): `transcribed_ids()` and `iter_export_batches()`
    `commit()` the reader session after their queries, so the
    REPEATABLE READ transaction the session autobegins is released
    before each chunk's S3 reads. On Aurora a view held open on a
    reader pins undo purge on the *writer*; a 20-minute job holding
    one during business hours was the one prod risk worth fixing
    now. Tests assert the release count.
  - "Download all" bookkeeping, no table: `new_export_id()`,
    `export_zip_key()`, `export_status_key()`, `export_download_url()`
    (7-day presign with the attachment filename/type baked in),
    `write_export_status()` / `read_export_status()` (missing object
    ⇒ `queued`; other S3 errors propagate). Constants
    `EXPORT_ALL_*`, `EXPORT_STATE_*`.
  - `EXPORT_S3_CONCURRENCY` — *deviation:* **10**, not 16. boto3's
    default connection pool is 10; at 16 the local run logged
    "Connection pool is full, discarding connection" for the surplus
    readers, so more than 10 buys nothing without a client config
    change.
- `app/services/aws.py`: `generate_presigned_download_url(bucket, key,
  *, filename, content_type, expires_in)` — a sibling of
  `generate_presigned_get_url`, not a change to it (tests patch the
  existing one by positional signature).
- `app/constants/permission.py`: `EXPORT_ALL_APPOINTMENT_CALLS =
  'Download All Appointment Calls'`.
- `app/schemas/appointment_calls.py`: `AppointmentCallExportRequest`
  (the ten filter fields; extra keys like `page` are ignored),
  `AppointmentCallExportStarted`, `AppointmentCallExportStatus`.
- `app/routes/appointment_calls.py`: `POST /appointment-calls/exports`
  (**202**; validates via `ListFilters.from_query` → 422; `launch_job`
  on `main`, `timeout_seconds=7200`, args `export-id` + `filters`
  JSON of the non-null body fields; `None` job id → 502) and
  `GET /appointment-calls/exports/{export_id}` (path `pattern` 32 hex
  → 422; `read_export_status` via `asyncio.to_thread`). Both behind
  `EXPORT_ALL_APPOINTMENT_CALLS`, registered before
  `/{sf_task_sf_id}`.
- `app/commands/appointment_calls_export.py` (+ registration in
  `commands/__init__.py`): `appointment-calls-export --export-id
  --filters`. Orchestration only: status `processing` → `run_export`
  into a temp file under the **reader** session, progress statuses,
  upload only when `exported > 0`, `complete` written after the
  upload (`size_bytes` 0 and no ZIP when nothing matched), any
  exception → `error` status + re-raise. Gotcha for tests/tooling: the
  package re-exports the typer function under the module's name (same
  as `zoominfo_exit`), so get the module via
  `importlib.import_module("app.commands.appointment_calls_export")`.
- Tests (`tests/test_appointment_calls.py`, 63 passing, whole file):
  round-2 export tests untouched (refactor guard); `_export_world()`
  seed shared; `TestTranscribedIds`, `TestRunExport` (members +
  progress, chunk order via `chunk_size=2`, empty archive, **parity**
  with `GET /export?ids=`, S3 failure propagates), `TestExportAllRoutes`
  (403 with the page permission only, 202 + presigned URL + exact
  `launch_job` kwargs with paging stripped, 422 before launch, 502,
  status queued/passthrough/other-error), `TestExportAllCommand`
  (status sequence + upload key + members, empty → no upload, upload
  failure and bad filters → `error`).
- Verified end to end locally: `python -m app.cli
  appointment-calls-export --export-id <hex> --filters
  '{"kinds":"pitch,pitch_follow_up","date_to":"2026-09-05"}'` against
  the volume seed → 302/302 transcripts, 245 KB ZIP, 5.9 s; status
  object `complete`; the pre-signed MinIO URL fetched from the host
  returned 200 with `Content-Disposition: attachment;
  filename="appointment-call-transcripts_2026-09-22.zip"` and a valid
  archive (302 members, header block + transcript).

### udab-client

- `src/constants/permissions.js`: `EXPORT_ALL_APPOINTMENT_CALLS`, in
  `APPOINTMENTS_PERMISSIONS` (role editor).
- `src/constants/appointment-calls.js`: `EXPORT_ALL_CONFIRM_ABOVE =
  10000`, `EXPORT_ALL_POLL_MS = 3000`, `EXPORT_ALL_STORAGE_KEY`,
  `buildExportParams({ filters, view })` (list params minus
  page/per_page/sort/dir), `readStoredExport(now)` (drops malformed
  or expired entries), `writeStoredExport`, `clearStoredExport`,
  `formatBytes`.
- `src/helpers/udab-api.js`: `requestAppointmentCallExport(params)`
  (POST), `getAppointmentCallExport(id)` (no global loader).
- `AppointmentCallsPage.vue`: behind `hasPermission(EXPORT_ALL_…)` —
  "Download all" (disabled while loading or `total` is 0) → confirm
  modal only when `total > 10,000` (inline `v-if`, no Teleport, no
  Bootstrap JS; "up to N calls") → POST → job in a ref +
  localStorage → `setInterval` poll every 3 s. Pill: "Queued…" /
  "Preparing…" / "Preparing 1,500 / 18,342"; `complete` with bytes →
  "Download ready (34.0 MB)" button (`window.open` on click) +
  "Dismiss"; `complete` with 0 bytes → info toast, cleared; `error` →
  error toast with the message, cleared; a failed poll is ignored
  (next tick retries). On mount, a stored unexpired job is resumed
  (polling unless already complete) — only when the permission is
  held. Polling stops on unmount. Filter/view changes leave a running
  export alone.
- Tests: constants (params, storage round-trip/expiry/malformed,
  bytes), api helpers (POST body + GET url), page (absent/present by
  permission; start → queued → processing → complete → click opens
  the URL → dismiss; threshold confirm; resume from storage; stored
  complete shows without polling, expired dropped, no permission
  → no resume; empty/error/failed-start toasts, transient poll
  failure). Whole client suite: 79 files, 1508 tests passing (in the
  `udab-client` container — `node_modules` lives there).
- Not done: a browser pass against the running stack (built against
  the API contract + tests, as in round 2).

### Follow-ups at PR time

Risk review (2026-09-22) — nothing here can take the API down as
long as the three prod facts below hold; they are assumed by
existing features but have never been written down as checked:

- **Prod checks (5 min in the console):** `RUN_JOBS_LOCAL` is 0 on
  the prod API (otherwise the job runs *inside* the API container);
  the Batch job definition's `READER_DB_CONNECTION_STRING` is the
  Aurora reader endpoint; the API signs S3 URLs with long-lived keys
  (7-day presign).
- **Stuck-job give-up (client, not built yet):** `launch_job` returns
  an id when Batch *accepts* the job, not when it runs. A job stuck
  RUNNABLE, or killed at the 2 h timeout mid-upload, leaves the pill
  at "Queued…"/"Preparing…" until the 7-day expiry. Lean: the page
  abandons a job still non-terminal 2 h after `requested_at` with a
  "try again" toast. Small; do before or right after merge.
- **Link exposure — needs a human decision:** each export is a
  7-day unauthenticated URL to the whole matching transcript corpus,
  kept in the requester's localStorage. Same mechanism as the
  existing 7-day audio links, much larger blast radius. A 24 h URL
  with 7-day retention still covers "download it tomorrow".
- Queue contention: see "Deferred" above.
- Assign `Download All Appointment Calls` to the CEO / CTO role(s).
- S3 lifecycle rule, 7 days, on `exports/appointment-calls/`.
- Confirm the API's S3 credentials are long-lived keys (7-day
  presign; audio playback already relies on this).
- The `main` Batch job definition: memory is fine (one 500-id chunk
  in memory, ZIP on disk); expect ~1–2 min "Queued…" on cold start.
- Client lint (`vue-cli-service lint --no-fix`) is clean for the
  changed files; the two `brace-style` errors it reports in
  `tests/helpers/udab-api.test.js` are on a pre-existing line.
