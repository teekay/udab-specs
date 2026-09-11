---
kind: notes
status: done
area: appointment-emails
updated: 2026-09-11
repos: [udab-server, udab-client]
summary: "Living reference: how the Appointment Calls queue works today (population, kinds, transcript state, export); specs are history."
---

# Appointment Calls queue — how it works today

Living doc. Update it when a decision or gotcha lands; the specs in this
folder are history. Paths are `udab-server/` unless stated. Verified
against the code on 2026-09-11 (branch `call-queue-2`). The appointment
*email* generation itself is Dani's surface and is not described here.

## Shape

One read view: `app/services/appointment_calls.py` + `app/routes/appointment_calls.py`
(server), `udab-client/src/pages/appointment-calls/*` + `src/constants/appointment-calls.js`
(client). Permission `VIEW_APPOINTMENT_CALLS` gates every route. No
work-state, no lifecycle, no writes — filters persisted per user via
`/view-preferences/appointment-calls`.

- **Population** (`population_filters()`): `sf_task` rows with
  `TaskSubtype = Call`, not deleted, `CallDisposition IN` the
  appointment **and pitch** buckets of `CALL_DISPOSITION_MAP`, on a
  Pipeline Client / Active account, `CreatedDate >= 2026-08-25 13:35 UTC`
  (`APPOINTMENT_CALLS_SINCE`, first auto-transcribe run — earlier
  calls were never transcribed and their vendor audio has expired, so
  no backfill is possible). Served by
  `ix_sf_task_disposition_created (CallDisposition, CreatedDate)`.
- **Kinds** — `booking | confirmation | follow_up | reschedule | pitch |
  pitch_follow_up`. Derived once at import from the fixed disposition
  lists (`KIND_BY_DISPOSITION` / `DISPOSITIONS_BY_KIND`); the kinds
  filter is a plain `IN` over the selected kinds' variants and the
  kind sort is a `CASE` over the same lists. **No `LIKE` in the access
  path** — data volume rules it out. A new disposition spelling needs
  a code change to `CALL_DISPOSITION_MAP` (and lands in a kind via the
  confirm/follow/resched chain, or pitch/pitch-follow-up for the pitch
  bucket). Pitch bucket is classified first because its strings contain
  "follow".
- **Transcript state** (`transcript_state()`): a `sp_call_transcript`
  row wins → `transcribed`; else the newest `sp_transcription_job_task`
  (highest id): completed/skipped **with** a key → `transcribed`,
  pending/transcribing → `transcribing`, failed or skipped-without-key
  → `failed` (unsupported vendor, permanent); else `pending` with a
  recording URL, `no_recording` without. Transcripts never regress.
- **Playback**: our saved copy (`audio_s3_key`, presigned 7 d) beats
  the vendor URL (may have expired). Saved audio is retained by an S3
  lifecycle rule; see call-queue.md.
- **Transcript key resolution** (flyout, download, export): newest job
  task with a non-null `transcript_s3_key`, else the conventional
  `sf-task-transcripts/{sf_task_sf_id}.txt`.

## Export (round 2)

- `GET /appointment-calls/{id}/transcript/download` → one `.txt`;
  `GET /appointment-calls/export?ids=a,b,c` → ZIP of the same `.txt`s
  (≤ 200 ids after de-dup, else 422; nothing exportable → **204**,
  the client's `api.download()` returns `false` and the page toasts).
  Ids outside the population or without a transcript are silently
  skipped — the population is authoritative.
- File = header block (Account, Contact, Rep, Team, Industry, Kind,
  Called in UTC, Duration m:ss, SF Task link; missing → `—`, lines
  never dropped), a 70-dash separator, then the transcript. Names:
  `<YYYY-MM-DD_HHMM>_<account-slug>_<sf_task_sf_id>.txt`;
  `appointment-call-transcripts_<YYYY-MM-DD>.zip`. No manifest.
- Client selection: **only `transcribed` rows are checkable** (the
  header checkbox selects the page's transcribed rows); the Set
  survives sort/paging and clears when filters change; cap 200 with a
  toast. Rationale and the rejected "all rows checkable" lean:
  call-queue-round-2.md.
- `KIND_LABELS` in the service duplicates the client's kind labels for
  the header — keep them in step.

## Local testing

- **Volume seed:** `docker exec udab-server python /scripts/local/seed_appointment_calls_volume.py`
  (`--clean` to remove; `--calls-per-day`, `--since`, `--accounts` to
  resize). Marker `SEEDVL`; deterministic. Defaults give ~445k `sf_task`
  rows, ~14k population rows with the full transcript-state mix, real
  transcript `.txt` objects and small WAVs in MinIO, summaries,
  highlights, appointment emails, seed AMs with teams and seed reps.
  Grants the "Appointment Calls" permission to every `sp_role`. The
  round-1 scenario seed (`seed_appointment_calls.py`, marker `SEEDAC`,
  8 hand-picked rows) is independent and can coexist.
- **Query plans:** `docker exec udab-server python /scripts/local/explain_appointment_calls.py`
  runs `EXPLAIN ANALYZE` on the routes' real statements. On the seeded
  DB (2026-09-11) every query uses `ix_sf_task_disposition_created`;
  the cost that remains is proportional to the population, not to
  `sf_task`: default page ~70 ms, count ~200 ms, `kind` sort ~80 ms,
  `account_name` sort ~360 ms, `transcript_state` sort ~470 ms
  (correlated job-task subqueries per population row), `/filters`
  ~850 ms (four DISTINCT passes over the population). The population
  grows ~1,200 rows/workday, so count, non-date sorts and `/filters`
  scale with it — the first candidates for a fast follow (cache
  `/filters`, or a materialised kind/state column) rather than a new
  index.
- **API without the UI:** the JWT middleware compares first/last name,
  email, superadmin and the *computed* permission list against the DB
  user, so a hand-minted token must be built from the `sp_user` +
  `sp_role` rows (and PyJWT here returns bytes — decode it).

## Gotchas

- Route order matters: `/export` and `/filters` are registered before
  `/{sf_task_sf_id}`.
- `transcript_text()` is `load_transcripts()` for one id — don't loop
  it for batches (it runs the full joined query each time).
- Tests must run through `scripts/test.sh` (ephemeral DB); the fixture
  prefixes (`00TACQ`, …) keep purges away from other tests' rows.
- The item shape's `kind` is a free `str` in the schema; the client
  falls back to rendering the raw value for unknown kinds.
