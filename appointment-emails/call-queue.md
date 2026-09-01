---
kind: spec
status: in-progress
area: appointment-emails
updated: 2026-09-01
repos: [udab-server, udab-client]
summary: "AM list of transcribed appointment calls: audio archive, team sync, API and client page. Built 2026-09-01, unmerged."
---

# Appointment Call Queue — one list of every transcribed appointment call

Status: ANALYSIS 2026-09-01. Q1/Q4/Q5 answered by Anna Clare Crews
2026-09-01 (see Decided); Q9 (audio retention) she is finding out; the
rest not yet sent. Prod profile of the appointment bucket added to
Analysis §0. No code on `call-queue` (branch = main at 28edfc2).
Entries marked *(lean)* are Tomas's proposed defaults.

Client ask (2026-09-01, verbatim shape): for account managers, a single
queue of every appointment call, filterable by **account, user, account
industry, date, team**, with quick sort on columns. Each entry: account
name, quick link to the call with a **play button**, full transcript,
summary, key highlights, link to the appointment email preview ("new
appointment briefing in AIQ"). Later: a **grade of the call** and a
**plain-language grade of the opportunity** ("excellent" vs
"borderline") — they are still working out what those are.

Builds on ../transcription/auto.md (the poller that transcribes
appointment + pitch calls minutes after they land), the highlights /
summary generator (`call-transcripts-generate`, PR #735 extended it to
all transcripts), and the appointment-email feature (`/appointments`
page + flyout with Preview tab).

## Open questions

### Client-facing (send before quoting)

Scope — decides volume and whether every row has an email link:

- [x] **Q1. What is an "appointment call"?** ANSWERED 2026-09-01 —
      "the call results handed over for appointments, for the
      transcription", i.e. the whole appointment bucket (see Decided and
      Analysis §0). Original question kept for context: Only the *booking* call
      (the Task matched to the `Appt A` flip / `Appt_Set_Date__c`), or
      every Task whose disposition is in the appointment bucket —
      follow-ups, confirmations, reschedules, the 38-variant list they
      approved 2026-08-24? Only booking calls get an appointment email;
      follow-ups never will. Pitches (`KDM Pitched`) are assumed OUT.
- [ ] **Q2. Same account scope as auto-transcription** (record type
      Pipeline Client, status Active)? Calls outside it are never
      transcribed, so they can't appear with content. And forward-only
      from go-live — no historical calls? (Orum audio for old calls is
      gone regardless.)
- [ ] **Q3. Calls with no recording** — now sharper after the prod
      profile: 17% of the bucket has no recording URL, and those are
      almost entirely `Appointment Confirmation` / `Appointment
      Follow-up` calls of 1–2 minutes. Show them as rows with
      "no recording / no transcript", or list only calls that have a
      recording? Lean: show them — the AM still wants to know the
      confirmation call happened.

"Queue" semantics — the biggest hole in the request:

- [x] **Q4. List or queue?** ANSWERED 2026-09-01: "Regular list. No
      lifecycle." Original: Do items get claimed / marked reviewed /
      dismissed, and drop off once handled? Per AM or shared? Does each
      AM see only their own accounts, or all? A real queue needs a
      work-state table (who, when, status) and a row-ownership rule; a
      list needs neither.
- [x] **Q5. Who are "account managers" in AIQ terms** — ANSWERED
      2026-09-01: one permission for the page in totality; no per-user
      data scoping initially; filters give each AM their view, and the
      page should **remember the last-used filters per user across
      logins**. Original: existing
      udab-client users? A new role? Defined by a Salesforce field
      (account owner)? Reuse the `Appointment Emails` permission or add
      a new one?

Filter definitions:

- [ ] **Q6. "User"** = the rep who made the call (Task owner), or the
      account owner (the AM)?
- [ ] **Q7. "Team"** = `Partner_Sales_Team__c` of the account owner
      (what the transcription API already means by `team`), or something
      else?
- [ ] **Q8. "Date"** = when the call happened, when the appointment was
      set, or when the meeting is scheduled? Default range on open?

Audio:

- [ ] **Q9. How long must the play button work?** PENDING — client is
      finding out (2026-09-01). Today's vendor URLs
      die in days (Orum) / ~30 days (CloudCall). Lean: propose "as long
      as the transcript" and ask for a retention period.
- [x] **Q10. Any policy constraint on us holding a second copy of call
      audio?** DECIDED 2026-09-01 (Tomas): vendors keep audio only for a
      limited time, so we copy it — not a client question. Retention
      period stays Q9.
- [ ] **Q11. "Quick link to the call"** — the SF Task record, the vendor
      recording page, or just the in-app player?

Content / UX:

- [ ] **Q12. Transcript display** — inline-expandable row, or click
      through to a flyout with tabs (Summary / Highlights / Transcript /
      Email preview)? Lean: flyout; a 10-min call is ~1,500 words.
- [ ] **Q13. Highlights are the same rows the appointment email uses** —
      an edit in either place shows in both. Should AMs edit highlights
      from the queue (with the feedback loop), or read-only?
- [ ] **Q14. Calls with no draft** (follow-ups, ineligible contact, not
      yet quality-reviewed): show "no briefing", or hide the link? For
      sent / canceled / expired drafts, show the status?
- [ ] **Q15. Row before content?** A call is transcribed minutes after
      it ends; summary + highlights trail by the generator's cadence.
      Show the row immediately with a "generating…" state, or only once
      complete? (Also: account/owner names arrive via the hourly SF
      stream — a row can be up to ~1 h behind its transcript.)
- [ ] **Q16. CSV export?** The Transcriptions tab has one; cheap to add.

Future grades — ask now so the schema leaves room:

- [ ] **Q17. Who produces the call grade and the opportunity grade** —
      the call-quality team (`Client_Scorecard_ID__c`/`_Name__c` exist
      on Task; the API-key `/api/transcription-jobs` was built for that
      team), an LLM prompt over the transcript, or a human in AIQ?
      Numeric, letter, or label + rationale? Filter/sort on it? Visible
      to whom?

### Internal (settle at implementation, lean stated)

- [ ] **Audio archive format.** PARKED 2026-09-01 (Tomas) pending Q9:
      with ~30-day retention the WAV bucket never grows past a few
      hundred GB rolling — keep storing vendor bytes as-is; with long /
      unlimited retention WAV is a no-go — transcode in the worker
      (batch job, time is not a concern). Plan when it lands: WAV
      (Orum) → AAC-LC via ffmpeg
      (`-c:a aac -b:a 64k -movflags +faststart`, keep both channels)
      stored as `.m4a`; already-compressed bodies (CloudCall MP3) pass
      through unchanged. Measured on a 5-min 16 kHz stereo file:
      WAV 18.3 MB → AAC 64k 2.4 MB (Opus 32k would be 0.94 MB but
      Ogg/Opus needs Safari ≥ 18.4 and is still reported buggy there;
      AAC/MP3 play everywhere, and at cents/GB the extra ratio buys
      nothing). ffmpeg into the worker image: `apt-get install
      --no-install-recommends ffmpeg` = +526 MB measured on
      python:3.12.1, or a static build (~42 MB download,
      johnvansickle) if image size matters. Note the archive covers
      pitch calls too, not just this queue's bucket.
- [ ] **Audio archive placement.** In the transcribe worker right after
      a successful Deepgram call (the URL is known live at that moment),
      vs. a separate sweeper over `sp_call_transcript` rows without
      audio. Lean: in the worker — one fetch while the URL is certainly
      alive; a sweeper would race Orum's expiry.
- [ ] **Team on the mirror.** `Partner_Sales_Team__c` lives on User in
      SF and is not a column on `sf_user`. Add it to the model + sync
      field list + migration, then a one-time `sfdc-sync --type user`.
      No alternative short of live SOQL per request (no).
- [x] **Queue state** — moot, Q4 = list. Replaced by: **per-user
      filter persistence.** Lean: a small `sp_user_view_preference`
      (user_id, page key, JSON) — generic so other pages can use it —
      written on filter change, read on page load.
- [ ] **Deep link into the appointment flyout.** `/appointments` opens
      the flyout on row click only; no URL addressing. Add
      `?email=<id>` handling to `AppointmentsPage.vue` so the queue can
      link straight to the Preview tab.

## Decided

Client (Anna Clare Crews, 2026-09-01):

- **Regular list, no lifecycle.** No claim/done state, no work-state
  table, rows never "pop out". Kills the internal queue-state item.
- **"Appointment call" = Task whose `CallDisposition` is in the
  appointment bucket handed over for transcription**
  (`CALL_DISPOSITION_MAP[appointment]`, 38 strings), on a Pipeline
  Client / Active account — the same population the poller transcribes.
  Booking calls, confirmations, follow-ups and reschedules are all in.
  In prod only five spellings actually occur (Analysis §0); the rest
  are historical typos that cost nothing to keep.
- **One page-level permission**, no per-user data scoping in v1.
  Filters do the scoping; **persist each user's last filter set across
  logins** (server-side per-user preference, not localStorage — they
  log in from wherever).
- Audio retention period: pending (Q9). Copying itself is decided (Q10).

Tomas (2026-09-01):

- **Ownership boundary — this feature is read-only over Dani's code.**
  Summaries/highlights (`call_transcript_generate.py`,
  `services/call_transcript.py`, `sp_call_transcript*` tables) and the
  appointment-email routes/process are Dani's; the transcribe worker,
  `transcription_jobs`, `sf_user`/sync and the SF mirror are Tomas's.
  Consequences: audio columns go on `sp_transcription_job_task` (the
  worker already stamps `transcript_s3_key` there), not on
  `sp_call_transcript`; grade columns are **not** reserved now (decide
  where they live when Q17 is answered — likely their own table);
  highlights are read-only in the queue for v1 (Q13 → no); the sweeper
  backlog/cadence is Dani's to fix, the queue just renders whatever
  state exists. Only shared touch: the `?email=<id>` deep link in
  `AppointmentsPage.vue` (Dani's page) — a ~10-line change to
  coordinate, or opened as a separate tiny PR.

Tomas (leans, not yet client-confirmed):

- *(lean)* **Audio gets archived at transcription time.** See Analysis
  §2 — without it the play button is a countdown.
- *(lean)* **Sub-kind column derived from disposition** — `booking`
  (`Appointment*` bare / typo spellings), `confirmation`, `follow-up`,
  `reschedule` — as a column and a filter. It is the difference between
  a 5-minute booking call with a transcript and a 1-minute confirmation
  with nothing, and it costs a 38-entry mapping.
- *(lean)* **One read endpoint, server-side filter/sort/paginate**,
  built on the SF mirror joins, not live SOQL. See Analysis §3.
- *(lean)* **The page is a new sidebar entry**, not a tab on
  `/appointments` — the audience (AMs) and the unit (a call, not a
  draft) both differ.
- *(dropped 2026-09-01)* Reserving grade columns on `sp_call_transcript`
  — that is Dani's model. Grades get their own home when Q17 is
  answered; the API/UI leave the columns nullable-optional so adding
  them later is additive.

## Assumptions (defaults that ship unless overridden)

1. **Row = one SF call Task** in the appointment disposition bucket
   (`CALL_DISPOSITION_MAP[appointment]`), on a Pipeline Client / Active
   account, `TaskSubtype = Call`, `IsDeleted = 0`, created on or after
   2026-08-25 13:35 UTC (poller go-live). Pitches excluded. Recording
   presence is not a criterion (Q3) — it decides the row's content
   state, not its existence.
2. **Content comes from `sp_call_transcript`** (transcript S3 key,
   summary, active highlights, `context_kind`). A Task with no row shows
   as "transcribing…" (if a `pending`/`transcribing` job task exists),
   "no recording" (no URL, or `failed`), or is hidden — per Q3.
3. **Email link = the `sp_appointment_email` row whose `sf_task_sf_id`
   matches**, any status; the link carries the draft status. No draft →
   "no briefing".
4. **Filters** resolve as: account = `sf_task.AccountId → sf_account`;
   user = `sf_task.OwnerId → sf_user` (Q6); industry =
   `sf_account.Industry` (with `Source_Industry__c` as sub-industry if
   wanted); date = `sf_task.CreatedDate` (Q8); team =
   `sf_user.Partner_Sales_Team__c` of the **account** owner (Q7), once
   synced.
5. **Sort** on every visible column, server-side, default newest call
   first.
6. **Play button** = presigned URL to our archived copy when present,
   else `playback_url(recording_url)` (vendor URL, may be dead), else
   disabled. The UI shows which it is.
7. **Read-only** except highlight editing, which reuses the appointment
   email endpoints if Q13 says editable.
8. **Permission**: new `View Appointment Calls` (constant in
   `constants/permission.py`), page-level, assigned to the AM role. No
   row-level scoping in v1 (confirmed). Last-used filters persisted per
   user server-side.

## Analysis

### 0. Prod profile of the appointment bucket (2026-09-01, last 30 days)

Aurora reader, `sf_task ⋈ sf_account`, Pipeline Client + Active,
`IsDeleted = 0`, `CallDisposition IN` the appointment bucket:

| | |
|---|---|
| Tasks | **5,683** (≈ 250 per weekday; 23 active days), all `TaskSubtype = Call` |
| Accounts / reps / contacts | 888 / 241 / 4,813 |
| With recording URL | 4,718 (83%) — Orum 2,468, CloudCall 2,249 |
| `Appt_Set_Date__c` populated | 5,683 (100% — contradicts auto.md's assumption that follow-ups lack it) |
| Duration | avg 291 s; 436 under 60 s (8%); 2,137 over 5 min (38%) |
| Linked appointment-email draft | 2,410 (42% of bucket; ~52% of `Appointment` rows) |
| Contacts with > 1 bucket Task | 696 of 4,813 (14%) — booking + confirmation/follow-up pairs |

By disposition — only five of the 38 spellings occur:

| CallDisposition | n | with URL | avg dur |
|---|---|---|---|
| `Appointment` | 4,622 (81%) | 4,602 (99.6%) | 316 s |
| `Appointment Confirmation` | 492 | **8** | 267 s |
| `Appointment Follow-up` | 459 | **3** | 117 s |
| `Appointment Follow Up` | 109 | 105 | 79 s |
| `Appointment Rescheduled` | 1 | 0 | — |

The URL-less rows are real dialer calls (`Outbound call from <rep>`,
`CallType = Outbound`, 45–130 s) that simply never got a recording URL —
neither `Call_Recording_URL_Public__c` nor `..._for_Portal__c`. So
"confirmation" and "follow-up" rows will overwhelmingly show **no
recording, no transcript**; that is Q3.

Off-list appointment-ish spellings on in-scope accounts: 3 rows in 30
days (`Confirmation`, `Confirmation Appointment`, `Appointment
Confirmaton`). The handover list is complete for practical purposes.

Coverage since auto-transcription go-live (first scheduled job
2026-08-25 13:35 UTC): 991 URL-bearing bucket Tasks → **988 transcribed
(99.7%)**, 2 skipped, 0 in flight. The transcript side is solid.

**Summaries/highlights lag badly**: of transcripts since go-live, 9,707
(appointment + pitch) have `context_kind = NULL`, zero generation
attempts — the all-transcripts sweeper (PR #735) made its first `call`
-framed pass on 2026-09-01 08:48 (482 done, 18 `ValueError` failures)
and is working a backlog at `MAX_PER_RUN = 50`. For the queue to feel
live, the sweeper's cap/cadence needs raising and the UI needs a
"generating…" state (Q15). The 18 `ValueError`s want a look regardless.

`Call_Recording_URL_for_Portal__c` **resolved**: CloudCall-only
(10,094 rows/30d, never Orum), a different endpoint
(`/customers/<user>/calls/recordingurl`) with the same `auth=` +
`expiryDate=` token scheme as the public URL — same ~30-day lifetime,
no help for playback longevity.

### 1. What exists — the request is mostly a read view over shipped data

Pipeline background (tables, scope constants, vendor facts): see `../transcription/NOTES.md`.

| Client item | Existing piece |
|---|---|
| Every appointment call transcribed | `auto-transcribe` poller: appointment + pitch dispositions, Pipeline Client/Active, ~minutes after the call |
| Full transcript | `sp_call_transcript.transcript_s3_key` (`.txt`, Caller/Prospect/Automated labels) + `.json` sidecar |
| Summary | `sp_call_transcript.summary` (+ `summary_model_id`, `summary_generated_at`) |
| Key highlights | `sp_call_transcript_highlight` — generated for every transcript (PR #735), soft-deletable, user-editable, feedback loop in `sp_call_transcript_feedback` |
| Appointment email preview | `sp_appointment_email.sf_task_sf_id` → call Task; `AppointmentFlyout.vue` Preview tab; `GET /appointment-emails/{id}/preview` |
| Play button | `recording_vendor.playback_url()` — Orum `?raw=true`, CloudCall direct MP3; already used by the Appointments page |
| Account / user / industry / date filters | `sf_account.Name`, `.Industry`, `.Source_Industry__c`; `sf_task.OwnerId`, `.CreatedDate`, `.Appt_Set_Date__c`; `sf_user` names |
| Column sort | `TranscriptionsTab.vue` `toggleSort`/`sortIcon` pattern |

Two framings matter for tone: `context_kind = appointment` when a live
draft exists (written for the person attending the meeting),
`context_kind = call` otherwise (summarized on its own terms). Both are
fine in a queue; the UI can show which.

**Team** is the one filter the mirror can't serve. `Partner_Sales_Team__c`
is only referenced in live SOQL (`services/transcription_jobs.py:111`,
`Account.Owner.Partner_Sales_Team__c`); `sf_user` has Title, Department,
CompanyName, UserRoleId but not the team field.

### 2. Audio — we do not have it, and the play button is a countdown

Vendor URL lifetimes and the no-audio-bytes fact are kept current in `../transcription/NOTES.md`.

`deepgram_transcribe.transcribe_from_url()` POSTs `{"url": recording_url}`
to `/v1/listen`; **Deepgram fetches the audio from the vendor**. No bytes
pass through us. S3 holds transcript `.txt` + `.json` only. The only
`audio_s3_key` in the codebase is `sp_call_transcript_local` — the
browser-extension / native-app path, a different feature.

Lifetimes of the vendor URLs (../transcription/v2.md §retention,
cloudcall-api-notes):

| Vendor | URL | Lifetime |
|---|---|---|
| Orum | `https://orum.com/recording/<id>?raw=true` (anonymous WAV, Range OK) | **days** — 400 at ~10 days (spike 2026-07-26) |
| CloudCall | `https://api.us.cloudcall.com/.../recordingurl?auth=…&expiryDate=…` | ~30 days (`URL_Expiry_Time__c`) |

So a queue whose play button targets the vendor URL works for about a
week per row while the transcript and summary beside it live forever.
The client's "quick link to the call with a play button" assumes
durable audio; they should hear plainly that it doesn't exist yet.

**Fix: archive at transcription time.** In `transcribe_calls_job`, after
Deepgram returns 200 for a Task, GET the same `deepgram_source_url` (it
was demonstrably alive seconds ago), stream to S3 under
`call-audio/<sf_task_sf_id>.<ext>`, and stamp `audio_s3_key` +
`audio_content_type` + `audio_bytes` on the **`sp_transcription_job_task`**
row (next to `transcript_s3_key`, which the worker already writes
there — keeps the change inside Tomas's code). Serve to the browser
with a presigned GET (same 7-day presign the transcript endpoints use).
Failure to archive is logged, never fails the transcription.

Cost, order of magnitude: appointment calls only (a few hundred/day),
~10 min average, Orum WAV ≈ 38 MB/call → single-digit GB/day → a few
dollars/month at S3 standard, growing linearly; transcoding to MP3 cuts
that ~10× if it ever matters. Lifecycle rule = whatever Q9 says.

Two things this does **not** solve, and the client should hear both:

- No backfill. Audio for calls transcribed before the archive ships is
  either already gone (Orum) or on a 30-day clock (CloudCall). The
  archive helps from the day it deploys.
- Untranscribable calls stay unplayable (no URL, expired before the
  poller reached it).

`Call_Recording_URL_for_Portal__c` is not an escape hatch — same
CloudCall token scheme and lifetime (Analysis §0).

### 3. The list endpoint

`GET /appointment-calls` (JWT + new permission), paged, server-side
filter and sort. One query over the mirror:

```
sf_task t
  JOIN sf_account a          ON a.sf_id = t.AccountId
  JOIN sf_user owner         ON owner.sf_id = t.OwnerId          -- call user
  LEFT JOIN sf_user am       ON am.sf_id = a.OwnerId             -- account owner / team
  LEFT JOIN sp_call_transcript ct ON ct.sf_task_sf_id = t.sf_id
  LEFT JOIN sp_appointment_email ae ON ae.sf_task_sf_id = t.sf_id
  LEFT JOIN sp_transcription_job_task jt (latest row per task, for pending/failed state)
WHERE t.CallDisposition IN (<appointment bucket>)
  AND t.TaskSubtype = 'Call' AND t.IsDeleted = 0
  AND a.RecordTypeId = <PIPELINE_CLIENT> AND a.Status__c = 'Active'
  AND t.CreatedDate >= <go-live>
```

Highlights are fetched per page (`call_transcript_id IN (...)`,
`deleted_at IS NULL`, ordered by position) — not joined, to keep the
row count sane. Response per row: task id, account {id, name,
industry}, call user, account owner, team, call date, duration,
disposition, vendor, `recording_playback_url` (archived → presigned,
else vendor), `transcript_state` (`transcribed` / `transcribing` /
`failed` / `no_recording`), `summary`, `highlights[]`, `context_kind`,
`appointment_email` {id, status} or null, and the reserved grade fields.

Transcript text is not in the list payload — `GET
/appointment-calls/{task_id}/transcript` returns it on demand (same
`read_s3_file` path as `appointment_email._load_transcript`).

Freshness: transcript rows appear minutes after a call; `sf_task` /
`sf_account` rows arrive through the hourly `sfdc-stream` (task joined
it per ../transcription/auto.md). The queue is therefore ≤ ~1 h behind
the call for name/owner columns. If minutes matter, the stream interval
is the knob (schedule, not code) — same answer as the URL-to-Aurora
question.

Existing SOQL-side account/team predicates are not reused: this view
must be fast, filterable and sortable over months of rows, which the
mirror gives and live SOQL does not.

### 4. The page

New sidebar entry **Appointment Calls** (`/appointment-calls`),
`VIEW_APPOINTMENT_CALLS` permission. Table with the client's columns,
`toggleSort` on each header (server-side sort param), filter bar
patterned on `AppointmentFilterBar.vue` (account multi-select from a
`/appointment-calls/accounts` helper like the existing
`/appointment-emails/accounts`, user select, industry select, team
select, date range), `Pagination` component.

Row click → flyout with tabs: **Summary**, **Highlights** (list; edit
controls only if Q13 = editable, reusing the appointment-email highlight
endpoints when a draft exists), **Transcript** (shared transcript
viewer component, already extracted per ../transcription/read-view.md R9),
**Player** (inline `<audio>` on the playback URL, plus the SF Task link
and vendor link). The "Appointment briefing" button opens
`/appointments?email=<id>` in a new tab on the Preview tab (needs the
deep-link addition in `AppointmentsPage.vue`).

Grade columns render as "—" until populated; sort/filter on them
enabled from day one so nothing changes when grades arrive.

### 5. Volume

Appointment-bucket Tasks on Pipeline Client/Active accounts: hundreds
per workday (the auto spec's ~1,200/day is appointment **and** pitch,
Orum dials dominating). Months of rows = tens of thousands — trivial
for MySQL with the existing `sf_task` indexes
(`ix_sf_task_created_date`, `ix_sf_task_who_deleted_disposition`) plus
one new composite on `(CallDisposition, CreatedDate)` if the plan shows
a scan.

### Out of scope (v1)

- Backfilling audio or transcripts for calls before go-live.
- Refreshing expired vendor URLs.
- Queue work-state (claim / reviewed / dismissed) — confirmed out, Q4.
- Row-level scoping by AM — confirmed out for v1, Q5.
- Grade production and storage (Dani's model is not touched; decide
  the home when Q17 lands).
- Sweeper backlog / cadence and the 18 `ValueError`s — Dani's feature;
  handed over 2026-09-01. The queue renders "generating…" meanwhile.
- Notifications (email/Slack on new appointment call) — not asked;
  worth a mention when answering, cheap on top of the poller.

## Implementation plan (once Q1–Q8 are answered)

| Repo / file | Change |
|---|---|
| `udab-server/app/models/sf_user.py` + sfdc sync field list + migration | add `Partner_Sales_Team__c`; one-time `sfdc-sync --type user --start-date 2000-01-01` after deploy |
| `udab-server/app/models/transcription_job.py` + migration | `TranscriptionJobTask.audio_s3_key`, `audio_content_type`, `audio_bytes`, `audio_archived_at` |
| `udab-server/app/commands/transcribe_calls_job.py` | after Deepgram success: stream `deepgram_source_url` → S3 `call-audio/<task>.<ext>`, stamp the row; log-only on failure |
| `udab-server/app/services/appointment_calls.py` | **new** — list query (filters, sort whitelist, paging), per-page highlight fetch, playback URL resolution (archived presigned → vendor) |
| `udab-server/app/routes/appointment_calls.py`, `schemas/appointment_calls.py` | **new** — `GET /appointment-calls`, `/accounts`, `/{task_id}/transcript`; JWT + `VIEW_APPOINTMENT_CALLS` |
| `udab-server/app/constants/permission.py` | `VIEW_APPOINTMENT_CALLS` |
| `udab-server` model + migration + route | `sp_user_view_preference` (user_id, page, JSON); `GET/PUT /me/view-preferences/{page}` — filter persistence per Q5 |
| `udab-server/tests/test_appointment_calls.py` | filters (each), sort whitelist, disposition bucket, account scope, transcript states, playback resolution, permission |
| `udab-client/src/pages/appointment-calls/*` | **new** page: table + filter bar + flyout (Summary / Highlights / Transcript / Player); sidebar + router + permission gate |
| `udab-client/src/pages/appointments/AppointmentsPage.vue` | `?email=<id>` deep link opens the flyout on Preview — **Dani's page; coordinate or separate PR** |
| Infra | none new — the archive rides in the existing worker; S3 lifecycle rule per Q9 |

Order: audio archive first (every day it isn't deployed is audio lost),
then team sync, then endpoint + page. The archive is independent of the
client's answers and can ship as soon as Q10 is cleared.

## Implemented (client, 2026-09-01)

Built against the API contract, not a running server; branch `call-queue`
in `udab-client`. Server section is appended separately.

Files added:

- `src/constants/appointment-calls.js` — kind / transcript-state labels
  and badge classes, `formatDuration` (m:ss), `defaultFilters`,
  `buildListParams` (filter state → query params), `viewPreferencesPayload`
  / `sanitizeViewPreferences` (what is stored under
  `/view-preferences/appointment-calls` and how untrusted stored values
  are validated back to defaults).
- `src/pages/appointment-calls/AppointmentCallsPage.vue` — table, sort,
  paging (25/50/100), inline row player, flyout host, preference
  hydration + debounced (500 ms) `PUT`.
- `src/pages/appointment-calls/AppointmentCallFilterBar.vue` — account /
  rep / industry / team / kind multi-selects (`CheckboxMultiSelect`,
  options from `/appointment-calls/filters`), date range
  (`VueDatePicker`, date-only, `yyyy-MM-dd`), recording tri-state select,
  debounced search, Clear filters.
- `src/pages/appointment-calls/AppointmentCallFlyout.vue` — Summary /
  Highlights / Transcript / Recording tabs, "Appointment briefing" button
  → `/appointments?email=<id>` in a new tab.
- Tests: `tests/constants/appointment-calls.test.js`,
  `tests/pages/appointment-calls/{AppointmentCallsPage,AppointmentCallFilterBar,AppointmentCallFlyout}.test.js`,
  `tests/pages/appointments/AppointmentsPageDeepLink.test.js`.

Files modified:

- `src/helpers/udab-api.js` — `getAppointmentCalls`,
  `getAppointmentCallFilters`, `getAppointmentCall`,
  `getAppointmentCallTranscript`, `getViewPreferences`,
  `putViewPreferences` (the preference calls never show the global loader).
- `src/constants/permissions.js` — `APPOINTMENT_CALLS = 'Appointment
  Calls'`, added to the `Appointments` permission group so it appears in
  the role editor.
- `src/router/index.js` — `/appointment-calls` (name
  `appointment-calls`), lazy, gated like `/appointments`.
- `src/layouts/AuthenticatedLayout.vue` — sidebar entry "Appointment
  Calls" under Appointments, same gating.
- `src/components/calls/TranscriptViewer.vue` — optional `text` prop:
  renders a transcript the parent already holds instead of fetching a
  URL. Existing URL behaviour unchanged.
- `src/pages/appointments/AppointmentsPage.vue` (Dani's page, ~12 lines)
  — `?email=<id>` opens the flyout after the list loads; uses the listed
  row when present, otherwise `GET /appointment-emails/{id}`. The flyout
  already resets to Preview on open, so no `initialTab` prop was needed.

Decisions the spec did not cover:

- **Persistence trigger.** The `PUT` is scheduled from the three explicit
  change points (filter bar `apply`, sort toggle, per-page change), not
  from a deep watcher, so half-typed search strings are not stored. The
  stored shape is `{ filters, sort, dir, per_page }`; `page` is never
  sent. Hydration happens before the filter bar mounts so its
  change-detection snapshot starts from the stored state, and a failed
  `GET` silently falls back to defaults.
- **Stored preferences are validated**, not trusted: unknown sort fields,
  dirs, page sizes, kinds or malformed date ranges fall back per field.
- **Sort direction on first click** is ascending for every column except
  `called_at`, which starts descending (newest first, the default view).
- **Unsortable columns.** Recording, Summary and Briefing have no server
  sort key in the contract, so their headers are plain.
- **Row player.** One inline `<audio>` at a time, rendered in a spacer row
  under the call; the button is disabled with a "No recording" tooltip
  when `playback_url` is null; an `archive` / `vendor` hint is shown next
  to it and spelled out under the player ("Vendor recording — link may
  expire").
- **Flyout refresh.** Opening the flyout silently re-fetches
  `/appointment-calls/{id}` so a summary generated since the list loaded
  shows up; the refreshed item is merged back into the table row. The
  transcript is fetched on first activation of the Transcript tab only;
  404 renders a state-specific empty message (queued / transcribing /
  failed / no recording), other errors offer Retry.
- **Empty summary/highlights** explain why (no recording, failed, still
  transcribing, queued) instead of a bare dash.
- **Dates** render through the shared `formatDate` (America/Chicago) with
  the same "CST" caption the Appointments table uses. Duration is m:ss
  with minutes uncapped (65:00).
- **Briefing column** shows the draft status badge as the link text and
  opens the Appointments page in a new tab (`router.resolve` href, so it
  survives a base-path change).

## Implemented (server, 2026-09-01)

Branch `call-queue` in `udab-server`. Read-only over Dani's tables
(`sp_call_transcript*`, `sp_appointment_email`); nothing under her files
was touched.

Migrations (chained off `4a504db504aa`, raw SQL, with downgrades):

- `c7e1a2b3d4f5` — `sp_transcription_job_task.audio_s3_key /
  audio_content_type / audio_bytes / audio_archived_at` (INSTANT).
- `d8f2b3c4e5a6` — `sf_user.Partner_Sales_Team__c` + index
  `ix_sf_user_partner_sales_team`. **Deploy note:** run
  `sfdc-sync --type user --start-date 2000-01-01` once after deploy; the
  nightly sync is incremental and leaves existing users NULL.
- `e9a3c4d5f6b7` — `sp_user_view_preference` (`user_id`, `page`, JSON
  `preferences`, unique `(user_id, page)`).

Files added:

- `app/services/appointment_calls.py` — population, filters, sort
  whitelist, per-page fetches (job tasks, highlights, drafts), playback
  resolution, `call_kind`, `transcript_state`.
- `app/schemas/appointment_calls.py`, `app/routes/appointment_calls.py` —
  `GET /appointment-calls`, `/filters`, `/{sf_task_sf_id}`,
  `/{sf_task_sf_id}/transcript`; JWT + `VIEW_APPOINTMENT_CALLS`.
- `app/models/user_view_preference.py`, `app/schemas/view_preference.py`,
  `app/routes/view_preference.py` — `GET/PUT /view-preferences/{page}`,
  any JWT user, own rows only.
- `tests/test_appointment_calls.py`, `tests/test_view_preference.py` —
  DB-backed (local docker MySQL via `scripts/test.sh`), fixture rows in
  the SF mirror + pipeline tables; S3 and the JWT user lookup faked.

Files modified:

- `app/commands/transcribe_calls_job.py` — after a Task completes, stream
  `deepgram_source_url` → S3 `call-audio/<sf_task_sf_id>.<ext>` and stamp
  the four audio columns; a row for the same Task that already has an
  archive is copied, not re-downloaded; any failure is a warning and the
  task stays `completed`. Summary log line now ends
  `audio_archived=N audio_skipped=N audio_failed=N`.
- `app/models/transcription_job.py` — audio columns, `CALL_AUDIO_S3_PREFIX`,
  `audio_s3_key(sf_task_sf_id, ext)`.
- `app/models/sf_user.py`, `app/commands/sfdc/sync.py` — team field
  (SOQL is `SELECT FIELDS(ALL)`, so only the two `record.get` sites
  changed).
- `app/services/aws.py` — `upload_s3_fileobj` (streaming) and
  `generate_presigned_get_url`.
- `app/constants/permission.py` — `VIEW_APPOINTMENT_CALLS = 'Appointment
  Calls'` (permissions are not enumerated anywhere else server-side).
- `app/main.py` — router registration. `tests/test_transcription_job.py`
  — archive tests; existing worker tests stub the vendor fetch.

Decisions the spec did not cover:

- **Population `WHERE`** is exactly the brief's: disposition bucket,
  `TaskSubtype = 'Call'`, `IsDeleted = 0`, Pipeline Client + Active,
  `CreatedDate >= 2026-08-25 13:35` (UTC-naive, as the mirror stores
  it). Owner and account-owner are LEFT JOINs (a Task whose user is not
  in the mirror still lists). `WhoId` resolves Contact first, then Lead.
- **`transcript_state` precedence:** a `sp_call_transcript` row wins;
  else the newest job task: `completed`/`skipped` with a key →
  `transcribed`, `pending`/`transcribing` → `transcribing`, `failed` or
  `skipped` without a key (unsupported vendor — permanent) → `failed`;
  else URL present → `pending`, none → `no_recording`. Sorting on it uses
  an equivalent SQL `CASE` (correlated latest-job-task subqueries), so
  it is server-side and orders alphabetically by label.
- **`kind`** filters and sorts on a SQL `CASE` twin of `call_kind()`
  (`confirm` > `follow` > `resched` > booking), not on the raw
  disposition string.
- **Playback** looks for the newest job-task row *with* an archive for
  the Task, not just the latest row, so a later `already_transcribed`
  dedupe row never hides an archived copy. Presign TTL 7 days.
- **`date_to`** as a bare date is `< next midnight`; as a datetime it is
  `<=`. Timezone-aware inputs are converted to UTC; bad values are 422.
- **Archive content types:** `audio/*` (wav/wave/x-wav → `wav`,
  `mpeg` → `mp3`, else the subtype) and `application/octet-stream`
  (`bin`); anything else (`text/html` player pages) is a failure. Bodies
  are spooled (16 MB in memory, then disk) and uploaded via
  `upload_fileobj`; 120 s timeout, redirects followed.
- **Highlights** ride on the list payload (active, by position) rather
  than a separate endpoint — one query per page.
- **`/view-preferences`**: page key `^[a-z0-9-]{1,50}$` (422 otherwise),
  body must be a JSON object, > 16 KB serialized → 413. A unique-key race
  on first write is retried as an update.
- **Timestamps**: `called_at`, `transcribed_at`, `summary_generated_at`
  are emitted with `Z` (UTC); `appointment_email.appt_scheduled_at` is
  passed through naive, as the Appointments API does.
- Not done: no composite `(CallDisposition, CreatedDate)` index on
  `sf_task` yet (spec §5 says "if the plan shows a scan") — measure on
  prod first.

## Verified end-to-end (local stack, 2026-09-01)

Seed: `udab-server/scripts/seed_appointment_calls.py` (8 calls on two
Pipeline Client/Active accounts, two reps, two teams; 2 s WAVs + transcripts
on MinIO; `--clean` removes). Driven through Chrome DevTools against
`udab-client` dev server + `udab-server` container, dev DB migrated to
`e9a3c4d5f6b7`.

Passed: sidebar entry + permission gate; list with every transcript state
badge, kind badge, briefing status link; inline player on an archived row
(presigned MinIO URL, 206, 2 s); flyout header/tabs — summary with framing
hint, 3 active highlights (soft-deleted one hidden), transcript with
speaker labels, recording tab with archive audio + SF/vendor links
(`_blank` + `noopener`); state-specific flyout messages for failed /
generating / no-recording rows; column sort (server-side); search;
every filter via API (`kinds`, `teams`, `industries`, `owner_ids`,
`date_from/to`, `has_recording`), 422 on unknown sort; `/filters`
options; preference PUT on change and hydration before the first list
fetch after reload; `?email=<id>` deep link opens the Appointments
flyout on Preview.

Fixed during verification: the sticky header sat 70 px over the first
row because the `overflow-x: auto` wrapper became the sticky ancestor.
Now page-sticky like the Appointments table; horizontal scroll (with
sticky dropped) only below 1400 px.

Not exercised: a live vendor URL (Orum/CloudCall) in the player — seed
URLs are fake; the worker's archive against a real recording.
