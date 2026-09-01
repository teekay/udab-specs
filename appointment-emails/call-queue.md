---
kind: spec
status: draft
area: appointment-emails
updated: 2026-09-01
repos: [udab-server, udab-client]
summary: "Analysis for an account-manager queue of transcribed appointment calls; needs audio archiving and client answers Q1-Q17."
---

# Appointment Call Queue — one list of every transcribed appointment call

Status: ANALYSIS 2026-09-01. Client questions drafted, not yet sent. No
code on `call-queue` (branch = main at 28edfc2). Nothing in Decided is a
client decision yet — entries marked *(lean)* are Tomas's proposed
defaults, to be confirmed or overridden by the answers.

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

- [ ] **Q1. What is an "appointment call"?** Only the *booking* call
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
- [ ] **Q3. Calls with no recording or a failed transcription** (~20% of
      Orum Tasks carry no URL; expired URLs fail at Deepgram) — show them
      as "no recording" rows, or leave them out entirely?

"Queue" semantics — the biggest hole in the request:

- [ ] **Q4. List or queue?** Do items get claimed / marked reviewed /
      dismissed, and drop off once handled? Per AM or shared? Does each
      AM see only their own accounts, or all? A real queue needs a
      work-state table (who, when, status) and a row-ownership rule; a
      list needs neither.
- [ ] **Q5. Who are "account managers" in AIQ terms** — existing
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

- [ ] **Q9. How long must the play button work?** Today's vendor URLs
      die in days (Orum) / ~30 days (CloudCall). Lean: propose "as long
      as the transcript" and ask for a retention period.
- [ ] **Q10. Any policy constraint on us holding a second copy of call
      audio** (consent, retention, region)? The vendors already store
      it; we'd be duplicating into our S3.
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

- [ ] **Audio archive format.** Store the vendor bytes as-is (Orum WAV
      16 kHz/16-bit/stereo ≈ 3.8 MB/min; CloudCall MP3) or transcode to
      MP3/Opus (~10× smaller, needs ffmpeg in the worker image). Lean:
      as-is first — S3 is cents/GB and the worker stays dependency-free;
      transcode later if the bucket bill ever matters.
- [ ] **Audio archive placement.** In the transcribe worker right after
      a successful Deepgram call (the URL is known live at that moment),
      vs. a separate sweeper over `sp_call_transcript` rows without
      audio. Lean: in the worker — one fetch while the URL is certainly
      alive; a sweeper would race Orum's expiry.
- [ ] **Team on the mirror.** `Partner_Sales_Team__c` lives on User in
      SF and is not a column on `sf_user`. Add it to the model + sync
      field list + migration, then a one-time `sfdc-sync --type user`.
      No alternative short of live SOQL per request (no).
- [ ] **Queue state (only if Q4 = queue).** New table
      `sp_appointment_call_review` keyed by `sf_task_sf_id`: status,
      reviewed_by_user_id, reviewed_at, note. Lean: ship the list first;
      add state as a follow-up once they've seen the list.
- [ ] **Deep link into the appointment flyout.** `/appointments` opens
      the flyout on row click only; no URL addressing. Add
      `?email=<id>` handling to `AppointmentsPage.vue` so the queue can
      link straight to the Preview tab.

## Decided

*(nothing client-confirmed yet; leans below)*

- *(lean)* **Audio gets archived at transcription time.** See Analysis
  §2 — without it the play button is a countdown.
- *(lean)* **One read endpoint, server-side filter/sort/paginate**,
  built on the SF mirror joins, not live SOQL. See Analysis §3.
- *(lean)* **The page is a new sidebar entry**, not a tab on
  `/appointments` — the audience (AMs) and the unit (a call, not a
  draft) both differ.
- *(lean)* **Reserve grade columns now** on `sp_call_transcript`:
  `call_grade` (String 20), `opportunity_grade` (String 20),
  `grade_rationale` (Text), `graded_at`, `graded_by` (user id or model
  id). Nullable, unused until Q17 is answered; one migration now beats
  one later with a UI change on top.

## Assumptions (defaults that ship unless overridden)

1. **Row = one SF call Task** in the appointment disposition bucket
   (`CALL_DISPOSITION_MAP[appointment]`), on a Pipeline Client / Active
   account, created on or after go-live of the poller. Pitches excluded.
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
8. **Permission**: new `View Appointment Call Queue` (constant in
   `constants/permission.py`), assigned to whichever role Q5 names. No
   row-level scoping in v1.

## Analysis

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
`audio_content_type` + `audio_bytes` on `sp_call_transcript`. Serve to
the browser with a presigned GET (same 7-day presign the transcript
endpoints use). Failure to archive is logged, never fails the
transcription.

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

Worth an internal check before answering Q9: `sf_task.
Call_Recording_URL_for_Portal__c`. The appointment-email matcher
prefers it over `Call_Recording_URL_Public__c`; if it is a longer-lived
URL the problem shrinks. Unverified.

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
- Queue work-state (claim / reviewed / dismissed) unless Q4 says queue.
- Row-level scoping by AM (each AM sees only their accounts) unless Q4/Q5
  require it.
- Grade production. Columns reserved only.
- Notifications (email/Slack on new appointment call) — not asked;
  worth a mention when answering, cheap on top of the poller.

## Implementation plan (once Q1–Q8 are answered)

| Repo / file | Change |
|---|---|
| `udab-server/app/models/sf_user.py` + sfdc sync field list + migration | add `Partner_Sales_Team__c`; one-time `sfdc-sync --type user --start-date 2000-01-01` after deploy |
| `udab-server/app/models/call_transcript.py` + migration | `audio_s3_key`, `audio_content_type`, `audio_bytes`, `audio_archived_at`; reserved `call_grade`, `opportunity_grade`, `grade_rationale`, `graded_at`, `graded_by` |
| `udab-server/app/commands/transcribe_calls_job.py` | after Deepgram success: stream `deepgram_source_url` → S3 `call-audio/<task>.<ext>`, stamp the row; log-only on failure |
| `udab-server/app/services/appointment_calls.py` | **new** — list query (filters, sort whitelist, paging), per-page highlight fetch, playback URL resolution (archived presigned → vendor) |
| `udab-server/app/routes/appointment_calls.py`, `schemas/appointment_calls.py` | **new** — `GET /appointment-calls`, `/accounts`, `/{task_id}/transcript`; JWT + `VIEW_APPOINTMENT_CALLS` |
| `udab-server/app/constants/permission.py` | `VIEW_APPOINTMENT_CALLS` |
| `udab-server/tests/test_appointment_calls.py` | filters (each), sort whitelist, disposition bucket, account scope, transcript states, playback resolution, permission |
| `udab-client/src/pages/appointment-calls/*` | **new** page: table + filter bar + flyout (Summary / Highlights / Transcript / Player); sidebar + router + permission gate |
| `udab-client/src/pages/appointments/AppointmentsPage.vue` | `?email=<id>` deep link opens the flyout on Preview |
| Infra | none new — the archive rides in the existing worker; S3 lifecycle rule per Q9 |

Order: audio archive first (every day it isn't deployed is audio lost),
then team sync, then endpoint + page. The archive is independent of the
client's answers and can ship as soon as Q10 is cleared.
