---
kind: notes
status: done
area: transcription
updated: 2026-09-01
repos: [udab-server, udab-client]
summary: "Living reference: how the transcription pipeline works today; specs are history."
---

# Transcription — how it works today

Living doc. Update it when a decision or gotcha lands; the specs in this folder are history. Paths are `udab-server/` unless stated. Constants quoted here were verified against `app/` on 2026-09-01.

## Pipeline

```
auto-transcribe (poller, every 1–2 min, EventBridge→Batch; app/commands/auto_transcribe.py)
  └─ transcription_jobs.create_job (app/services/transcription_jobs.py; also called by POST /api/transcription-jobs)
       ├─ SOQL against Salesforce LIVE (never the sf_task mirror) → snapshot into sp_transcription_job + sp_transcription_job_task
       └─ launch_job → transcribe-calls-job --job-id N (app/commands/transcribe_calls_job.py, CONCURRENCY = 5)
            ├─ Deepgram POST /v1/listen {"url": recording_url}  (app/services/deepgram_transcribe.py) — Deepgram fetches the audio from the vendor; we never hold audio bytes
            ├─ transcript_postprocess.build_transcript_result (app/services/transcript_postprocess.py)
            ├─ S3 abstrakt-intelligence/sf-task-transcripts/{sf_task_sf_id}.txt + .json  (transcript_s3_key / transcript_json_s3_key in app/models/transcription_job.py)
            └─ _ensure_call_transcript → call_transcript.get_or_create_for_task → sp_call_transcript row (non-fatal if it fails)
call-transcripts-generate (sweeper; app/commands/call_transcript_generate.py) → summary + highlights via Bedrock (app/services/bedrock.py) onto sp_call_transcript / sp_call_transcript_highlight
consumers: appointment emails (app/commands/appointment_email, pure consumer — never generates), udab-client Calls → Transcriptions tab (JWT routes app/routes/transcription_job.py), API-key /api/transcription-jobs (+ /api/sf-tasks/{id}/transcript) for the call-quality team
```

- `reconcile-transcription-jobs` (app/commands/reconcile_transcription_jobs.py): manual; marks pending/processing jobs whose Batch container exited as failed. Read endpoints never mutate state.
- Deepgram API key: `sp_setting` key `deepgram-api-key` (`Setting.DEEPGRAM_API_KEY`). Poller exits loudly if it is missing.
- Presigned transcript URLs: 7 days (SigV4 max); GET re-mints on every call. Transcripts stay in S3 indefinitely.

## Tables

| Table | Notes |
|---|---|
| `sp_transcription_job` | one per POST / per poller dispatch; `status` pending → processing → completed / failed (`failed` = job-level crash only; per-task failures still end `completed`); `call_result` appointment / pitch; `start_date`/`end_date` NOT NULL (poller stores the window's calendar dates); `filters_json` (poller sets `source: "scheduled"`; absent = manual); `aws_batch_job_id` |
| `sp_transcription_job_task` | one per SF Task per job; `status` pending → transcribing → completed / skipped / failed; `skip_reason` `already_transcribed` or `unsupported_vendor` (old rows may carry `not_cloudcall_url`); `recording_url` snapshot; `transcript_s3_key`; `deepgram_request_id` (set before postprocess, so failed-with-id = paid); index `ix_transcription_job_task_sf_id_status` |
| `sp_call_transcript` | canonical per-Task row: `sf_task_sf_id` (idempotency key, get-or-create), `sf_contact_id`/`sf_lead_id`, `recording_url`, `transcript_s3_key`, `transcribed_at`, `summary` + `summary_model_id`/`summary_generated_at`, `highlights_generated_at`/`highlights_model_id`, `context_kind` (`appointment` when a live draft exists, else `call`), generation bookkeeping `generation_claimed_at`/`generation_attempts`/`generation_error` |
| `sp_call_transcript_highlight` | per-transcript highlights: `position`, `title`, `detail`, `origin`, soft-delete `deleted_at`/`deleted_by_user_id` |
| `sp_call_transcript_feedback` | user feedback loop on highlights: `feedback_text`, `before_highlights`/`after_highlights`, `applied_diff`, `model_id`, `error_message` |
| `sp_call_transcript_local` | the extension/native-app live-transcription path (`app/models/call_transcript_local.py`). Different feature; it is the only table with `audio_s3_key` |

At most one transcript per SF Task, ever: all job rows for a Task share one S3 key. To force a re-transcription, delete the Task's `completed` `sp_transcription_job_task` row and re-request.

## Scope rules (code constants — business rules are never in `sp_setting`)

- Dispositions: `CALL_DISPOSITION_MAP` in `app/schemas/transcription_job.py` — `appointment` = the client's spelling-variant list incl. follow-ups, `Appointment Confirmation`, `Appointment Confirmed` (all confirmed IN 2026-08-24); `pitch` = `KDM Pitched` + `Pitch Follow - Up` / `Pitch Follow-Up` / `Pitch Follow Up`. SOQL `CallDisposition IN (...)`. The API enum stays two values.
- Accounts (poller only): `Account.RecordTypeId IN (SfAccount.PIPELINE_CLIENT = '012A0000000kZfwIAE')` (`app/models/sf_account.py`) and `Account.Status__c = 'Active'`. The POST accepts the same as optional `accountRecordTypeIds` / `accountStatus`.
- Date basis: poller uses `CreatedDate >= now − --lookback-hours` (default 24) with `ORDER BY CreatedDate DESC LIMIT MAX_SOQL_ROWS` (10,000, `transcription_jobs.py`). The POST keeps the `Appt_Set_Date__c` calendar range (`startDate`/`endDate`) or `createdSince`.
- Eligibility: `Call_Recording_URL_Public__c != null` — re-evaluated against SF live every tick, so a URL stamped later is picked up on a later tick.
- Claimed exclusion (`claimed_sf_task_ids`): skip Tasks with a row in `CLAIMED_STATUSES = (completed, pending, transcribing)`, a `skipped/unsupported_vendor` row (permanent), or a `failed` row newer than `FAILED_RETRY_AFTER_HOURS = 6`. Worker re-checks completed-only as a last line.
- Caps: poller `MAX_TASKS_PER_CHUNK = 200` (`cap_mode="truncate"`, overflow waits for a later tick, logged as `deferred=`); on-demand `MAX_TASKS_PER_JOB = 1000` (`app/routes/api/transcription_job_api.py`, SOQL `LIMIT cap+1`, HTTP 400 "narrow your filters" when exceeded).
- Single-flight (poller): `identical_batch_job_active()` guard, then `reconcile_batch_status` on in-flight scheduled jobs; if one is still pending/processing, exit without creating anything. One dispatch per tick; appointment/pitch scan order alternates by minute parity. Manual POSTs still dispatch their own worker.
- Scheduled runs write no `already_transcribed` skip rows (`record_skips=False`); `unsupported_vendor` skips are recorded once in a dispatch-less job born `completed`. Empty tick = one log line, no rows. A POST matching zero pending tasks records its job as `completed` and dispatches nothing.

## Vendors (`app/services/recording_vendor.py`)

| Host | Vendor | Channel swap | Deepgram URL | Retention |
|---|---|---|---|---|
| `api.us.cloudcall.com` | cloudcall | no (ch0 = rep) | as stored (direct MP3, token in query) | ~30 days; `URL_Expiry_Time__c` populated (95%) |
| `orum.com` | orum | **yes** (ch0 = prospect; empirically derived, re-verified 2026-07-26) | `?raw=true` appended (anonymous WAV, Range OK) | **days** — 400 at ~10 days (spike 2026-07-26); `URL_Expiry_Time__c` empty |
| anything else | — | — | task `skipped` / `unsupported_vendor` | e.g. `orum-playground.fox-pangolin.ts.net`, someone's sandbox writing into prod SF |

- Vendor is derived from the URL host, never stored. The `[Orum]` Subject prefix is unreliable (89% of non-`[Orum]` recording URLs are Orum) and is not used.
- ~20% of Orum Tasks carry no recording URL at all; Orum has no API, so they are untranscribable. Forward-looking feature: no backfill of old Orum calls; expired recordings surface as `failed` with the Deepgram fetch error (no Deepgram spend).
- `playback_url()` (same module) is the in-browser player URL (Orum `?raw=true`, CloudCall direct) used by the Appointments page.
- `Call_Recording_URL_Public__c` is the pipeline's field. The appointment-email matcher (`app/commands/appointment_email/process.py`) prefers `Call_Recording_URL_for_Portal__c` and falls back to Public; **the Portal URL's lifetime is UNVERIFIED** — do not assume it outlives the Public one.
- Dual-channel is assumed everywhere; the worker logs a warning (`_warn_if_not_dual_channel`) when Deepgram metadata reports < 2 channels (labels may be wrong, transcript still stored). Separately, postprocess falls back to per-channel flat text when the response has no `utterances`.

## CloudCall URL stamper (`app/commands/stamp_cloudcall_urls.py`, `app/services/cloudcall.py`)

- Every minute (EventBridge→Batch, guarded by `identical_batch_job_active()`): SOQL live for Tasks with `synety__Call_Session_Id__c != null AND Call_Recording_URL_Public__c = null AND synety__Actual_Date_Time_of_Call__c >= now − --lookback-minutes` (default 120). Zero candidates → exit without touching CloudCall.
- CloudCall: `https://ng-api.us.cloudcall.com`, `POST /v3/auth/login` (customer tier, `LicenseKey` header) then `GET /v2/customers/{user}/calls?from&to` **without `leg`** (listing window opens `LISTING_MARGIN_MINUTES = 10` earlier). Creds `cloudcall-license-key` / `cloudcall-username` / `cloudcall-password` in `sp_setting` (env `CLOUDCALL_*` as transitional fallback). Login is still one person's (`cgooding@`); a dedicated API user is an open ask.
- Match rule (verified 10/10, 2026-07-30): `SessionID == synety__Call_Session_Id__c`, then exactly one record with `Leg == 1` and `CallRecordingAvailable`. Anything else → skip, retried next tick; the client's SF batch (`:00/:15/:30/:45`) remains the backstop. Never `leg=c`, never closest-wins, never leg 2 (different audio).
- Stamp = `PATCH Task.Call_Recording_URL_Public__c`, then best-effort mirror `UPDATE sf_task ... WHERE sf_id = ? AND URL IS NULL` (`_update_mirror`). `task` is in the hourly `sfdc-stream` type list (`app/commands/sfdc/stream.py`, default `--hours 2`), so the mirror sees new Tasks and stamped URLs within ~1 h regardless. Never logs auth bodies or full recording URLs.

## Deepgram request and output

- `DEEPGRAM_PARAMS`: `model=nova-3`, `multichannel`, `diarize`, `smart_format`, `punctuate`, `utterances` (all true); no `keyterm`; `DEEPGRAM_TIMEOUT_SECONDS = 600`. `nova-3` is a moving alias (accepted). A separate `DICTATION_PARAMS` set exists for a non-call path.
- Postprocess: channel → role (`Caller`, `Prospect`; swap per vendor), automated-voice relabel to `Automated` (opening-IVR rule + phrase list `app/constants/automated_phrases.py`), interleave by start time splitting containers at interruptions (`OVERLAP_TOLERANCE_SECONDS = 0.6`), fallback to per-channel flat text. Output `.txt` = `[MM:SS] Speaker: text` per turn; `.json` = `{transcript, utterances, audio_duration_seconds, model, deepgram_request_id}` (stored, not API-exposed).
- Cost (`app/services/deepgram_billing.py`, estimate only, never stored): `RATE_USD_PER_BILLED_MINUTE = 0.004345`, `BILLED_CHANNELS = 2` (multichannel bills each channel), `SF_ROUNDING_BIAS_SECONDS = 0.4`; duration from the `sf_task.CallDurationInSeconds` mirror join. Paid task = `completed OR deepgram_request_id IS NOT NULL`, excluding `already_transcribed` copies. Ceilings from v2 (June 2026 volumes): ≈$370/mo old CloudCall-only scope, ≈$700/mo both vendors (the standing run-rate assumption), ≈$2,250/mo transcribe-everything. Per-run figures + runs/tasks CSV: `spend-report.md`, Transcriptions tab.
- Generator: `MAX_PER_RUN = 50`, `CONCURRENCY = 5`, `MAX_ATTEMPTS = 5`, `CLAIM_STALE_MINUTES = 30`, `DEFER_HOURS = 2`; generates for every transcript (framed for the meeting attendee when an appointment draft exists, otherwise on the call's own terms).

## API surface

- API key (`X-AIQ-API-KEY`, `app/routes/api/transcription_job_api.py`): `POST /api/transcription-jobs`, `GET /api/transcription-jobs/{id}` (progress only until completed, then tasks + presigned URLs), `GET /api/sf-tasks/{sfTaskId}/transcript` (read-only lookup, 404 distinguishes never/in-progress/failed). Bad key = HTTP 200 `{"unauthorized": ...}` (platform quirk). Client guide: `udab-server/docs/transcription-api-howto.md`.
- JWT + `VIEW_CALLS` (`app/routes/transcription_job.py`): `GET /transcription-jobs` (filters `start_date`/`end_date`/`source`/`status`/`call_result`; per-job `cost`, `summary` with `total_cost_usd`, `jobs_by_status`, `tasks_matrix`), `GET /transcription-jobs/{id}/tasks`, `GET /transcription-jobs/export/runs.csv`, `.../export/tasks.csv`.

## Gotchas

- Never run pytest against the dev DB — `cd udab-server && ./scripts/test.sh tests/...` (ephemeral migrated `udab_test_*` DB; `FRESH=1` rebuilds). `docker compose exec fastapi pytest` hits the real `udab` DB.
- `Appt_Set_Date__c` is a Date (day granularity) and follow-up/confirmation Tasks largely lack it — the on-demand `startDate`/`endDate` window silently misses them; only `CreatedDate`/`createdSince` surfaces them.
- SOQL `CallDisposition` comparison is case-insensitive but **not** whitespace-insensitive: `Pitch Follow - Up` ≠ `Pitch Follow-Up`; every spacing variant must be in the constant.
- The SOQL `LIMIT` runs before claimed-exclusion — hence the separate 10,000 guard + `ORDER BY CreatedDate DESC` for the poller (fresh calls first; a 1,000-cap on already-done rows would starve new ones).
- `sp_setting` is site-wide configuration (credentials, API keys) only; disposition lists, phrase lists and tolerances are code constants — deploy to change them.
- Salesforce POSTs occasionally take ~20 s (query-plan variance); use a 60 s+ client timeout. The `/api/*` middleware answers 200 on a bad key.
- `udab-server/docs/transcription-api-howto.md` still says follow-up and confirmation calls are not included — stale since 2026-08-24 (they are in the constant now).
- Extension / native-app live transcription (`sp_call_transcript_local`, `deepgram-key-provisioning.md`) is a different pipeline; don't conflate `audio_s3_key` there with this one (which stores no audio).
- Two workers can still race on the same Task (manual POST + poller tick); cost is one duplicate Deepgram call, never a wrong transcript.

## Open threads

- Refreshing expired CloudCall URLs (30-day shelf life) — the stamper's resolution path could do it; the fetch-on-demand design in `v2.md` slice 4 is the better home if needed.
- Per-end-client `keyterm` vocabulary — no source decided (SF custom field vs collation); `keyterm` is nova-3-only and httpx `params` must be a list of tuples to repeat it.
- Call grade / opportunity grade — columns reserved in `../appointment-emails/call-queue.md` (lean), producer undecided (Q17 there).
- Audio archiving at transcription time (we hold no bytes today) — proposed in `../appointment-emails/call-queue.md` §2, needs a policy answer on retaining a second copy.
- Dedicated customer-tier CloudCall API user; whether the client's SF batch overwrites a non-null URL and who stamps `URL_Expiry_Time__c` (lean: nobody).
- Deepgram Management API reconciliation — revisit when the client moves onto a Deepgram plan (re-run `scripts/check_duration_match.py`, update the rate constant).
- Orum sandbox host (`orum-playground`) writing into prod SF — ownership never asked.

## History

- `cloudcall-transcription.md` — original on-demand API: job tables, POST/GET, Deepgram by URL, S3 `.txt`, Batch worker, dedup by Task, crash detection (superseded by v2 on params/scope).
- `read-view.md` — udab-client Transcriptions tab + JWT list/tasks endpoints; shared `TranscriptViewer`.
- `deepgram-key-provisioning.md` — server-minted short-lived Deepgram tokens for the native app (the live-transcription path, not this pipeline).
- `v2.md` — diarized, labeled, interleaved transcripts; disposition variant list; Orum via `?raw=true` + channel swap; vendor by host; cap 50 → 1000; `/api/sf-tasks/{id}/transcript`; slice 4 CloudCall matching research (SessionID + Leg 1).
- `cloudcall-api-notes.md` — client's reverse-engineered CloudCall auth/listing, with the verified corrections.
- `cloudcall-url-stamper.md` — `stamp-cloudcall-urls` per-minute job stamping SF (mirror stamp added later by auto).
- `auto.md` — `auto-transcribe` poller, `create_job` service extraction, CreatedDate window, claimed exclusion, single-flight, chunk cap, `source` filter in the UI, `task` in `sfdc-stream`.
- `spend-report.md` — estimated cost per run/task, summary matrix, CSV exports.
- `udab-server/docs/transcription-api-howto.md` — client-facing API guide (lives with the API, not here).
