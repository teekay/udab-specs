# CloudCall Recording Transcription

## Problem

The client's call quality team scores calls made by sales reps. They need call recordings from CloudCall (not Orum) transcribed via Deepgram. Two primary use cases:

1. **Appointment calls** — reviewing recordings where `CallDisposition = 'APPOINTMENT'`
2. **Pitch calls** — reviewing recordings where `CallDisposition = 'KDM Pitched'`

These use cases serve different teams and must not be accidentally mixed — the endpoint requires an explicit call type selection.

Recordings live on Salesforce Task records in the `Call_Recording_URL_Public__c` field. These are direct audio URLs hosted by CloudCall (`https://api.us.cloudcall.com/...`) with auth tokens that expire after ~30 days (`URL_Expiry_Time__c`).

SF Tasks are not synced on a regular schedule, and the client needs data fresher than 24 hours. The endpoint queries Salesforce live at request time.

## Architecture

```
Client ──POST /api/transcription-jobs──► udab-server
                                            │
                                            ├── SOQL query ──► Salesforce (live, LIMIT cap+1)
                                            ├── Reject if > cap ("narrow your filters")
                                            ├── Create job + job_task rows (snapshot all needed fields)
                                            ├── launch_job() ──► AWS Batch (or local subprocess)
                                            └──► { jobId, summary }

Client ──GET /api/transcription-jobs/{id}──► udab-server
                                                └──► { status, progress; tasks + presigned URLs when done }

Background job (AWS Batch / local subprocess):
  for each pending job_task:
    ├── re-check dedup (another job may have completed it)
    ├── POST recording URL to Deepgram /v1/listen
    ├── Store transcript text in S3
    └── Update job_task row
```

No sf_task involvement: everything the job and the client need is snapshotted onto the job tables at creation time. (If we later want SF Tasks locally for other consumers, that's a separate concern — note that `sf_task` upserts require full records, i.e. `FIELDS(ALL)` fetches batched at ≤200 rows per SOQL limits.)

## Salesforce Data Model (verified against live data)

### Key fields on Task

| Field | Purpose | Example |
|---|---|---|
| `CallDisposition` | Call result — the primary filter | `"APPOINTMENT"`, `"KDM Pitched"` |
| `Call_Recording_URL_Public__c` | Direct CloudCall audio URL with auth token | `https://api.us.cloudcall.com/accounts/.../calls/.../recordingurl?auth=...&expiryDate=...` |
| `URL_Expiry_Time__c` | Recording URL expiry (~30 days from creation) | `2026-08-05T21:15:07` |
| `Subject` | Contains `[Orum]` prefix for Orum calls | `"Outbound call from Joe Enright to Kayla Gosselink"` (CloudCall) |
| `Appt_Set_Date__c` | Date filter — the "start/end date" from the spec | `2026-07-06` |
| `AccountId` | Linked Account | SF 18-char ID |
| `WhoId` | Linked Contact/Lead | SF 18-char ID |
| `CallDurationInSeconds` | Call length | `140` |

### Traversals for filters

- **Account Owner**: `Task → Account → Owner` (the User who owns the Account, not the Task owner)
- **Team**: `Account.Owner.Partner_Sales_Team__c` on the Account Owner's User record (e.g. "Space Calls", "Callstars", "Tune Squad")

### SOQL query

```sql
SELECT Id, Call_Recording_URL_Public__c, URL_Expiry_Time__c
FROM Task
WHERE Appt_Set_Date__c >= {start_date}
  AND Appt_Set_Date__c <= {end_date}
  AND CallDisposition = '{disposition}'
  AND (NOT Subject LIKE '%[Orum]%')
  AND Call_Recording_URL_Public__c != null
LIMIT {cap + 1}
```

Optional filter clauses appended dynamically:

```sql
  AND Account.OwnerId = '{account_owner_id}'
  AND Account.Owner.Partner_Sales_Team__c = '{team}'
  AND AccountId = '{account_id}'
  AND WhoId = '{contact_id}'
```

### Values confirmed from live data

- `Result__c` is NOT "Call Result" — it only contains "In Process" (7.5M records) and null (603). Useless for filtering.
- `CallDisposition` IS "Call Result" — `"APPOINTMENT"` (135,019 records with recordings), `"KDM Pitched"` (535,394). SOQL `=` is case-insensitive for this field (verified: all casing variants return identical counts), so no special handling needed.
- `Contact__c` is null on most records — use `WhoId` for contact filtering.
- Non-`[Orum]` subject lines still sometimes have `orum.com` recording URLs. The Subject filter is necessary but not sufficient to exclude Orum — we must also check the URL domain is `api.us.cloudcall.com` (not `orum.com`).

## Design Decisions

### Call type as mandatory enum

The `callResult` parameter is required and accepts exactly two values: `"appointment"` or `"pitch"`. This prevents accidental cross-contamination between the two use cases per the client spec.

If more CallDisposition values are needed in the future, they are added to this enum.

### Job size cap

`MAX_TASKS_PER_JOB = 50`, deliberately tight. The SOQL query runs with `LIMIT 51`; if 51 rows come back, the POST is rejected with HTTP 400: "More than 50 tasks match — narrow your filters (e.g. filter by team or account owner)." Unfiltered single-day APPOINTMENT volume already exceeds 50 (measured), so in practice this forces the call quality team to pull one team's or one rep's calls at a time — which matches their scoring workflow. It also bounds Deepgram spend and keeps POST fast and payloads small.

### No local sf_task sync

Tasks are NOT upserted into `sf_task`, and no convenience/display fields are snapshotted. The feature needs exactly two pieces of SF data per task: the Task Id (dedup key, S3 key, client-facing identifier) and the recording URL (Deepgram input, snapshotted so jobs are self-contained). The GET response identifies calls by SF Task Id only — the quality team cross-references in Salesforce, which they have open anyway.

Deferred, deliberately: if the client later wants context columns in the response (account name, rep, duration), decide then between adding snapshot columns or a best-effort sf_task sync inside the background job (`FIELDS(ALL) WHERE Id IN (...)` — trivial at cap 50) + join. Neither is needed now.

### Deduplication by SF Task ID

Each SF Task produces at most one transcript. Before calling Deepgram, check if a `sp_transcription_job_task` row with the same `sf_task_sf_id` already exists:

- **Completed in prior job**: reuse its `transcript_s3_key`, mark this row `skipped` (already_transcribed). Include presigned URL in results.
- **Pending/transcribing in another job**: create a `pending` row in this job anyway. The background job re-checks dedup right before calling Deepgram — the other job may have finished by then. Worst case, Deepgram gets called twice and S3 overwrites with identical content.
- **Failed in a prior job**: retry — create a `pending` row. The recording URL may have been temporarily unavailable.

### Recording URL validation

1. Do NOT skip based on `URL_Expiry_Time__c` — just try the URL. CloudCall may honor it past the stated expiry, and if not, Deepgram returns an error we catch and mark as failed. Log expired-looking URLs at job creation for diagnostics (from the SOQL result; not stored).
2. Check URL domain is `api.us.cloudcall.com` — Orum URLs (or any other domain) are recorded as `skipped` rows with `skip_reason = "not_cloudcall_url"` rather than silently excluded, so the client can see why a task wasn't transcribed.

### Transcript storage: S3, plain text only

Store transcripts as plain text in S3, not MySQL. Follows the existing pattern from `CallTranscriptService`.

- **Bucket**: `abstrakt-intelligence` (existing)
- **Key**: `sf-task-transcripts/{sf_task_sf_id}.txt`
- **One file per SF Task** — flat namespace, globally unique by sf_id

Plain text only for now. Diarization (speaker labels) is deferred until the client requests it — if that happens, we add `diarize=true` to the Deepgram call and store the raw response JSON alongside the txt.

### Presigned URLs: 7-day maximum

AWS SigV4 presigned URLs cap at 7 days (`X-Amz-Expires` ≤ 604800) — 30-day links are not possible. Instead: `GET /transcription-jobs/{id}` mints fresh URLs on every call, so the client re-polls whenever they need working links. Transcripts stay in S3 indefinitely. We use `expires_in=604800` (the max).

### Job dispatch: AWS Batch, re-runnable CLI

Uses the existing `launch_job()` pattern. A CLI command `transcribe-calls-job --job-id=N` processes the job. Locally, `RUN_JOBS_LOCAL=1` runs it as a subprocess.

**Crash detection**: on every GET, if the job is still `pending` or `processing` and has an `aws_batch_job_id`, ask AWS Batch via the existing `check_job_status(aws_batch_job_id)` (`app/services/aws.py`). (`pending` is included because a container can crash before writing `processing`.) Batch reports `SUCCEEDED`/`FAILED` only after the container has exited, and the CLI writes the job's final status before exiting — so "container exited + job row still `processing`" unambiguously means the worker died. In that case GET marks the job `failed` with error "worker exited without completing". No timestamp guessing involved.

Caveats:
- Skip reconciliation for `local-…` job IDs (local dev subprocesses aren't visible to Batch).
- Wrap the boto3 call in `asyncio.to_thread` — `check_job_status` is declared async but blocks.
- Batch retains job records ~7 days; after that `describe_jobs` returns empty and the helper reports FAILED — which is the correct verdict for anything still `processing` at that age anyway.

**Crash recovery**: the CLI is safely re-runnable — it picks up rows in `pending` OR `transcribing` state (a `transcribing` row only exists mid-flight or after a crash; re-processing one is harmless thanks to dedup and idempotent S3 writes). Task rows are left untouched by crash detection, so after a detected crash an operator re-runs `transcribe-calls-job --job-id=N`, which resumes the leftovers and sets the job back to `completed`. Re-dispatch is deliberately manual — auto-relaunch could loop forever on a poison job.

### Deepgram server-side transcription (new)

Existing Deepgram integration is client-side only (token provisioning for the native app). This feature adds server-side transcription:

```
POST https://api.deepgram.com/v1/listen?model=nova-3&smart_format=true
Authorization: Token {api_key}
Content-Type: application/json

{"url": "{recording_url}"}
```

The API key is read from `sp_setting` via `Setting.get_value(db, Setting.DEEPGRAM_API_KEY)`.

Deepgram fetches the audio directly from the CloudCall URL — we don't download it ourselves.

Response payload includes `results.channels[0].alternatives[0].transcript` — the plain text we store.

Implementation notes:
- `/v1/listen` with a URL is synchronous and slow for long calls — set a generous httpx timeout (10 min), not the default.
- Process tasks with modest concurrency (asyncio semaphore, ~5 concurrent) — 50 sequential calls at ~30s each would take 25+ minutes for no reason. Each concurrent task needs its OWN SQLAlchemy async session — sessions are not safe for concurrent use.
- Verified by spike (`scripts/deepgram_cloudcall_spike.py`): CloudCall URLs serve direct MP3, Deepgram ingests them without issues — see Risks section.

## Data Model

### New table: `sp_transcription_job`

| Column | Type | Notes |
|---|---|---|
| `id` | INT, PK, auto | |
| `call_result` | VARCHAR(20), NOT NULL | `"appointment"` or `"pitch"` |
| `status` | VARCHAR(20), NOT NULL | `"pending"`, `"processing"`, `"completed"`, `"failed"` |
| `start_date` | DATE, NOT NULL | Appt_Set_Date__c range start |
| `end_date` | DATE, NOT NULL | Appt_Set_Date__c range end |
| `filters_json` | JSON, nullable | Serialized optional filters (account_owner_id, team, account_id, contact_id) |
| `total_tasks` | INT, NOT NULL, default 0 | Total SF Tasks matched at creation |
| `aws_batch_job_id` | VARCHAR(255), nullable | AWS Batch job ID |
| `error_message` | TEXT, nullable | Job-level error if failed |
| `created_at` | DATETIME | |
| `updated_at` | DATETIME | |

Per-status counts (transcribed/skipped/failed/pending) are NOT stored — they are derived via `GROUP BY status` on `sp_transcription_job_task`. No increment logic, no drift.

Job `status` semantics: `completed` means the processing loop finished, even if some individual tasks failed (visible in task statuses). `failed` is reserved for job-level crashes (unhandled exception, DB loss).

### New table: `sp_transcription_job_task`

| Column | Type | Notes |
|---|---|---|
| `id` | INT, PK, auto | |
| `job_id` | INT, FK → sp_transcription_job | |
| `sf_task_sf_id` | VARCHAR(18), NOT NULL | Salesforce Task ID |
| `recording_url` | TEXT, NOT NULL | Snapshot of Call_Recording_URL_Public__c |
| `status` | VARCHAR(20), NOT NULL | `"pending"`, `"transcribing"`, `"completed"`, `"skipped"`, `"failed"` |
| `transcript_s3_key` | VARCHAR(255), nullable | S3 key when completed |
| `skip_reason` | VARCHAR(50), nullable | `"already_transcribed"`, `"not_cloudcall_url"` |
| `error_message` | TEXT, nullable | Deepgram error details |
| `created_at` | DATETIME | |
| `updated_at` | DATETIME | |

Index: `(sf_task_sf_id, status)` — for dedup lookups.

## API

### POST /api/transcription-jobs

Create a transcription job. Queries SF live, creates job rows, dispatches background processing.

**Request:**

```json
{
  "callResult": "appointment",
  "startDate": "2026-07-01",
  "endDate": "2026-07-06",
  "accountOwnerId": "005...",
  "team": "Space Calls",
  "accountId": "001...",
  "contactId": "003..."
}
```

| Field | Required | Notes |
|---|---|---|
| `callResult` | Yes | `"appointment"` or `"pitch"` — enum, no other values |
| `startDate` | Yes | Appt_Set_Date__c >= |
| `endDate` | Yes | Appt_Set_Date__c <= |
| `accountOwnerId` | No | SF User ID — filters on Account.OwnerId |
| `team` | No | Partner_Sales_Team__c value — filters on Account.Owner.Partner_Sales_Team__c |
| `accountId` | No | SF Account ID |
| `contactId` | No | SF Contact/Lead ID (WhoId) |

**Response (created):**

```json
{
  "success": true,
  "jobId": 42,
  "totalTasks": 15,
  "toTranscribe": 11,
  "alreadyTranscribed": 3,
  "skipped": 1
}
```

**Response (over cap, HTTP 400):**

```json
{
  "success": false,
  "error": "More than 50 tasks match — narrow your filters (e.g. filter by team or account owner)."
}
```

**Server-side steps:**

1. Validate: `startDate <= endDate`; Deepgram API key exists in `sp_setting` (reject with 500-level error if missing — don't dispatch a job doomed to fail)
2. Build SOQL query from filters, `LIMIT 51`
3. Execute via `Sfdc.query()` (60s+ timeout — see Risks)
4. If 51 rows returned → HTTP 400, no job created
5. Create `sp_transcription_job` row
6. Create `sp_transcription_job_task` rows, snapshotting the recording URL:
   - non-CloudCall URL → `skipped` (`not_cloudcall_url`)
   - transcript already exists (dedup) → `skipped` (`already_transcribed`), reuse `transcript_s3_key`
   - otherwise → `pending`
7. Dispatch via `launch_job(queue="main")` → `python -m app.cli transcribe-calls-job --job-id=42`
   - If `launch_job()` returns `None` (submit failed): mark the job `failed` with error "dispatch failed" — do not leave it `pending` forever
8. Return job summary

### GET /api/transcription-jobs/{id}

Poll job status and retrieve results.

**Response (in progress)** — progress only, no task list (avoids generating presigned URLs on every poll):

```json
{
  "success": true,
  "jobId": 42,
  "callResult": "appointment",
  "status": "processing",
  "progress": {
    "total": 15,
    "transcribed": 8,
    "skipped": 4,
    "failed": 0,
    "pending": 3
  }
}
```

**Response (completed)** — includes the task list with fresh presigned URLs (valid 7 days; re-call GET for new ones):

```json
{
  "success": true,
  "jobId": 42,
  "callResult": "appointment",
  "status": "completed",
  "progress": {
    "total": 15,
    "transcribed": 11,
    "skipped": 4,
    "failed": 0,
    "pending": 0
  },
  "tasks": [
    {
      "sfTaskId": "00TRj...",
      "status": "completed",
      "transcriptUrl": "https://s3.../sf-task-transcripts/00TRj...?X-Amz-..."
    },
    {
      "sfTaskId": "00TRj...",
      "status": "skipped",
      "skipReason": "already_transcribed",
      "transcriptUrl": "https://s3.../sf-task-transcripts/00TRj...?X-Amz-..."
    },
    {
      "sfTaskId": "00TRj...",
      "status": "failed",
      "errorMessage": "Deepgram: REMOTE_CONTENT_ERROR — could not fetch recording URL"
    }
  ]
}
```

Skipped-but-already-transcribed tasks still get a presigned URL — the transcript exists, we just didn't re-request it. Failed tasks expose `errorMessage` and no URL.

Note for API consumers: the `/api/` middleware returns HTTP 200 with `{"unauthorized": "Invalid API Key"}` on a bad/missing `X-AIQ-API-KEY` (platform quirk — not a 401). Clients should check for the `unauthorized` key.

## Background Job (CLI command)

Command: `python -m app.cli transcribe-calls-job --job-id=42`

### Processing loop

```
set job status = 'processing'

for each sp_transcription_job_task where status IN ('pending', 'transcribing'):
    # 'transcribing' rows are stale leftovers from a crashed run — safe to redo
    1. Re-check dedup: transcript completed by another job since creation?
       → yes: copy transcript_s3_key, mark 'skipped' (already_transcribed)
    2. Set status = 'transcribing'
    3. POST to Deepgram /v1/listen with recording URL (httpx timeout: 10 min)
    4. Extract transcript text from response
    5. write_s3_file(bucket, f"sf-task-transcripts/{sf_task_sf_id}.txt", transcript)
    6. Update job_task: status='completed', transcript_s3_key=...

    On Deepgram error:
       → Update job_task: status='failed', error_message=...

(tasks processed with asyncio semaphore, ~5 concurrent)

When loop finishes:
    → Set job status = 'completed'
On job-level exception:
    → Set job status = 'failed', error_message=...
```

### Error handling

- Deepgram 4xx/5xx: mark individual task as failed, continue with others
- Recording URL unreachable/expired (surfaces as Deepgram error): mark as failed with descriptive error, continue
- Job-level failure (DB connection, unhandled exception): set job status to `"failed"` with error message
- Worker died without writing final status: detected on GET via Batch reconciliation (see Crash detection above); job marked `failed`
- Recovery in both cases: re-run `transcribe-calls-job --job-id=N` — the loop is idempotent and resumes leftovers

## Implementation Plan

### New files in udab-server

| File | What |
|---|---|
| `app/models/transcription_job.py` | `TranscriptionJob` + `TranscriptionJobTask` SQLAlchemy models |
| `app/schemas/transcription_job.py` | Pydantic request/response schemas |
| `app/services/deepgram_transcribe.py` | Server-side Deepgram transcription — `transcribe_from_url(api_key, recording_url) → str` |
| `app/routes/api/transcription_job_api.py` | POST create + GET status endpoints (under `/api/` prefix for API-key auth) |
| `app/commands/transcribe_calls_job.py` | CLI command for background execution |
| `migrations/versions/xxx_add_transcription_job_tables.py` | Alembic migration |

### Changes to existing files

| File | Change |
|---|---|
| `app/main.py` | `include_router(transcription_job_api.router)` |
| `app/cli.py` | Register the `transcribe-calls-job` command |

### Not changed

- `sf_task` model and sync code are untouched — this feature does not read or write `sf_task`.

## Risks to verify during implementation

- ~~**SOQL performance**~~ **MEASURED** (spike, `scripts/sf_soql_timing.py`, 16-query matrix): typically 0.3–0.8s including the cross-object team filter; occasional outliers of 7–16s (SF query-plan/cache variance, not correlated with range width). Implications: give the SF call in POST a 60s+ timeout, and document that POST can occasionally take ~20s.
- ~~**Deepgram × CloudCall fetch**~~ **VERIFIED** (spike, `scripts/deepgram_cloudcall_spike.py`): CloudCall URLs serve a direct MP3 (200, no redirects, `content-type: audio/mp3`); Deepgram `/v1/listen` transcribed a real 140s call with 0.999 confidence in seconds. URL-based ingestion works as designed.
- **Cap vs real volume** (resolved): unfiltered single-day APPOINTMENT volume exceeds 50 — intentional; the cap forces per-team/per-rep pulls. See Resolved Questions.

## Resolved Questions

- [x] **CallDisposition casing**: SOQL `=` is case-insensitive for this field (verified against live data). No special handling.
- [x] **Presigned URL TTL**: client wanted 30 days; AWS caps SigV4 at 7 days. Resolution: 7-day URLs + GET re-mints fresh ones on every call.
- [x] **Job retention**: keep rows indefinitely, no purge logic.
- [x] **Concurrent jobs**: no overlap prevention. Dedup prevents wasted Deepgram calls regardless.
- [x] **Job size cap**: 50, kept deliberately tight. The consumer is the call quality team scoring reps' calls — they should be pulling one team's or one rep's calls at a time, not a firehose. Unfiltered 1-day APPOINTMENT volume already exceeds 50 (measured), so the cap effectively forces filtering by team/owner, which matches the intended workflow. The 400 error message should say so: "narrow your filters (e.g. filter by team or account owner)".
- [x] **sf_task sync**: deferred — live SF query only; not needed by this feature. Revisit only if future requirements want task context locally.
- [x] **Convenience fields** (account name, call duration, URL expiry): dropped from the snapshot — the feature strictly needs only Task Id + recording URL. Response identifies calls by SF Task Id; the quality team cross-references in SF.
- [x] **Diarization**: plain text only for now; add `diarize=true` + raw JSON storage only if the client requests it.
- [x] **Crash recovery**: automatic detection on GET via AWS Batch status reconciliation (`check_job_status()` already exists); recovery via re-runnable CLI, manually re-dispatched.
