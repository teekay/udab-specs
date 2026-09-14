---
kind: notes
status: draft
area: appointment-emails
updated: 2026-09-14
repos: [udab-server, udab-client]
summary: "Delta analysis of the client's appointment/pitch console mockups vs. what the platform, SF and AI pipeline have today."
---

# Appointment & Pitch console mockups — delta analysis

Beginning of a new spec. Analyzed 2026-09-14 against the code on main
(post round-1 call queue, round 2 `call-queue-2` in progress). Source
mockups from the client (2026-09-11 batch):

- `appointment-console (1).html` — appointment calls + AI grading layer
  (opportunity grade, HEART stars, DARTS gate, flags, coaching, urgency,
  decision-maker level, agreement strength) + meeting logistics (meeting
  date/type, Google satellite/street view of the building) + a
  "client-safe view" toggle that hides rep scoring so the page can be
  shared live on a client call.
- `pitch-console.html` — pitch calls recast as a callback-mining work
  queue: reason to call back, callback due/overdue chips, "meat on the
  bone" heat 1–5, appointment-ask detection with timestamps, objections
  + feel/felt/found, 9-point talk-track adherence checklist, coaching
  notes, rep talk share.

Both consoles sit exactly on the shipped Appointment Call Queue
population (appointment + pitch dispositions, Pipeline Client/Active,
since `APPOINTMENT_CALLS_SINCE` 2026-08-25). They are the "later: grade
of the call + plain-language opportunity grade" the client deferred in
round 1 ([call-queue.md](call-queue.md)), now made concrete. Shared
chrome in both: filter bar (date, client, industry, rep, account owner),
free-text search, sortable table, row-click detail drawer, play button,
"open full transcript" link.

## Bucket 1 — readily available today (plumbing exists; UI + joins)

- **Population, filters, sort, playback, transcripts.** Company,
  contact + title (`sf_contact.Title`), client, industry, rep, team,
  call date, duration (`sf_task.CallDurationInSeconds`), saved-audio
  play (presigned `audio_s3_key` beats expired vendor URL), transcript
  flyout/download/export, per-user filter persistence — all shipped in
  the queue ([NOTES.md](NOTES.md)).
- **Account owner.** Mockups use "PSM 1–3" placeholders and the footer
  says "in production this should pull from the Salesforce account
  owner" — we have it: `sf_account.OwnerId` → `sf_user`. New column +
  filter, no sync work.
- **Meeting date, "date set", meeting type.** From the appointment-email
  feature, joined via the existing `appointment_email.sf_task_sf_id`
  match: `appt_scheduled_at` (Appt_Scheduled_Date__c + Time__c
  snapshot), `occurred_at` (when booked), and `Phone_In_Person__c`
  (already fetched into `contact_snapshot` — this is the mockup's
  "In person / Phone" meeting-type chip). **Coverage caveat:** only
  appointment calls that matched an appointment-email row have these;
  pitch calls and unmatched bookings don't. The "Date by: meeting date
  vs date set" filter toggle needs this join in the access path.
- **Building satellite / street view.** Already built for appointment
  emails: `appointment_email.building_data` stores geocode / solar /
  street-view payload subsets, Google API key in production,
  `resolve_address()` (company snapshot, else contact Mailing*) exists.
  The mockup footer asks for exactly this (Static/Street View API
  instead of free embeds). Surfacing job, not new integration.
- **Summary + key-moments raw material.** `sp_call_transcript.summary`
  + `sp_call_transcript_highlight` (title/detail/position) exist per
  call, with a generation pipeline (model id tracking,
  regenerate-with-feedback). Stored transcripts are diarized
  `[MM:SS] Caller/Prospect:` lines (see
  `app/services/transcript_postprocess.py`), and the full result JSON
  (utterances with start offsets) is persisted to S3 alongside the
  `.txt`. So timestamped "key moments" and verbatim quotes are
  extractable. Current highlights have **no timestamps** — a
  prompt/schema change, not new infrastructure.
- **Client-safe toggle, drawer, prospect email.** Trivial UI; email is
  synced on `sf_contact.Email`.

Worth telling the client: the mockups' worst rows ("Company not
captured", "Eden Vista (name unclear in transcript)", "Company matched
by address") are artifacts of extracting identity from transcripts. Our
queue already knows the authoritative account/contact from the SF Task
(`WhoId`/`AccountId`) — we beat the mockup there for free.

## Bucket 2 — in Salesforce / derivable, needs modest work

- **Callback status (overdue / due in N days).** The pitch mockup's own
  footer concedes it "should read the next task or activity in
  Salesforce". `sf_task` is synced, so "has a follow-up been logged /
  when is it due" is derivable locally — but semantics need defining
  (which task types/subtypes count, open vs completed, per contact or
  per account). Bigger point: the shipped queue is deliberately
  read-only with no work-state; a callback queue with overdue states
  pulls toward lifecycle/writes (who claimed it, was it done). That is
  a real scope decision, not a column.
- **Meeting attendee** ("Meeting with Adam from TP Mechanical", "Owner:
  Taylor or Jake"). Not in anything we sync. Could be an SF field to
  ask the client about (Task has `Set_By_del__c`, `Event_Outcome__c` —
  nothing obvious); otherwise it's AI extraction from the transcript.
- **Free-text search** across prospect/contact/notes/AI fields. Doesn't
  exist on the queue. The ready
  [../transcription/search-endpoint.md](../transcription/search-endpoint.md)
  spec is adjacent (transcript full-text for the call-quality team) but
  not this; searching AI-derived fields implies those fields live in
  queryable columns, which feeds the Bucket-3 schema design.
- **Client minimums / qualification thresholds** (the "~10 seats;
  verify client minimum" flag). Needs a per-client config that doesn't
  exist anywhere yet.

## Bucket 3 — new AI extraction (feasible; this is the actual project)

Everything graded or judged on either console is per-call LLM
extraction we don't do today:

- Appointment console: opportunity grade (Excellent → At risk), HEART
  stars (Homework/Engagement/Ask/Relevance/Tie-down, 1–5 each), DARTS
  gate (pass/partial/fail × 5 with evidence notes), urgency 1–3,
  decision-maker level (DM/influencer/weak/unconfirmed + evidence),
  agreement-to-meet strength (strong/solid/soft/reluctant + quote),
  pain/need, timeline, opportunity size key-values, current vendor,
  reason for meeting, golden-question asked, flags, meeting brief,
  coaching note.
- Pitch console: "meat on the bone" heat 1–5, reason to call back,
  callback-window extraction from the conversation ("prospect said
  early 2027") + suggested callback date + next-move note,
  appointment-ask detection with timestamps + quotes, objection
  detection (type, quote, rep response) + feel/felt/found usage,
  objection-handling rating, 9-point talk-track adherence checklist
  (yes/partly/no/NA per item + %), flags, coaching note, key moments.

Pipeline precedent exists (auto-transcribe →
`call-transcripts-generate`, model tracking, regenerate-with-feedback),
so this is schema + prompts + a generation job, not new architecture.
The real cost is elsewhere:

- **Calibration + review workflow.** Every field is a claim shown to
  AMs and — via the client-safe view — potentially to clients live.
  Needs a QA loop, an "AI-derived" label, and probably a human-review
  gate before anything client-facing (the At-risk row in the mockup is
  itself an example: "review before this reaches the client").
- **Volume.** Population grows ~1,200 calls/workday; extraction cost,
  latency target (minutes after transcription?), and re-grade policy
  (model upgrades, prompt changes) need a line in the spec.
- **Talk-track adherence** could in principle lean on the real Talk
  Track link (`sf_task.Talk_Track_Session_Id__c`), but the mockup's
  9-point rubric is a fixed sales-methodology checklist (stated
  recorded line, open questions, ask, push past first no, dated next
  step…) — pure AI grading either way. Where does the rubric live:
  hardcoded, per-client, per-talk-track?

## Bucket 4 — fantasy, or dishonest with current data

- **Rep talk share %.** Stored utterances keep only start offsets, no
  ends, and the raw Deepgram response isn't retained. Going forward
  it's a small pipeline change (persist utterance ends or per-channel
  talk time from the Deepgram response); for already-transcribed calls
  only a word-count approximation is possible. As mocked (exact
  percentages, "Rep talk 81%" flags), not backable today.
- **Counted micro-metrics.** "Questions: 1 open, 2 closed", "1 no
  before ending", half-star HEART averages ("Average 3.9 stars").
  Countable by an LLM but noisy enough that precise numbers are false
  confidence. Propose dropping or coarsening in the spec.
- **Free Google Maps iframes.** Mockup's own footer concedes this;
  production path (Static/Street View API via building_data) exists.
- **Callback math assuming no follow-up logged.** As coded in the
  mockup it ignores SF activity entirely and would show false
  overdues; Bucket 2 fixes it.
- **Transcript-derived company identity.** See Bucket 1 — we don't
  need it and shouldn't copy it.

## Structural question to settle first

Two new pages, or views of the existing Appointment Calls page? Round 2
([call-queue-round-2.md](call-queue-round-2.md)) already brings pitch
calls and the Kind filter into the one page; the population and ~80% of
the columns are shared. Lean: **one surface**, with the AI "insights"
layer as a separate generation pipeline + schema that the page joins
onto (and the pitch-specific callback columns appearing for pitch
kinds), rather than parallel consoles. The client-safe toggle and the
callback work-state question are the two features that most strain the
current "read view, no writes" shape.

## Open questions for the client

- Are these consoles for AMs only, or shown to clients (client-safe
  view suggests live screen-shares)? Drives the review-gate design.
- Where do the rubrics (HEART, DARTS, 9-point checklist) come from —
  fixed Abstrakt methodology, or per-client? Who owns changes?
- Meeting attendee ("Adam from TP Mechanical"): is that tracked in SF
  anywhere, or should it be extracted from the call?
- Callback queue: is "mark as done / snooze" expected, or is reading
  SF next-activity enough?
- Which AI fields must be exact vs indicative? (Talk share, question
  counts, star averages — see Bucket 4.)
