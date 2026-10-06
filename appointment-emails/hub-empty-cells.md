---
kind: spec
status: done
area: appointment-emails
updated: 2026-10-06
repos: [udab-server, udab-client]
summary: "Replace every Hub dash with a reason: five kinds of empty counted on PROD, one word per cause, one sweeper fix."
---

# Account Management Hub — what an empty cell says

Status: DONE 2026-10-06 — merged and deployed to PROD as udab-server
#796 ("Call sweeper improvements") and udab-client #356 ("Report more
accurate reasons for empty cells in the Hub"). See "Implemented".

Client ask (2026-10-06, verbatim): "If we don't have a value because
we didn't detect one or do not have one, please put that in the field
(example: instead of - it would say 'not detected' or 'n/a'."

The ask conflates two things the client actually cares to tell apart:
"we looked and the call had nothing" versus "we could not look". On the
Hub those are different fields, different pipelines, and different
futures. This spec classifies every dash on the page by cause, counts
each cause on PROD, and proposes one word per cause.

## Where the dashes come from

Surface: `udab-client/src/pages/appointment-calls/AppointmentCallsPage.vue`
(table), `AppointmentCallInsightCell.vue` (AI columns),
`AppointmentCallFlyout.vue` (detail), placeholders in
`src/constants/appointment-calls.js` (`INSIGHT_PLACEHOLDERS`,
`formatDuration`, `formatMeetingTime`, `kindLabel`). Data:
`udab-server/app/services/appointment_calls.py` (`_build_item`) over
`sp_call_insight` (sweeper `call-insights-generate`,
`app/services/call_insight.py`).

Five classes of empty, verified against the code and PROD on
2026-10-06:

1. **Not on the call (final).** The model was asked and found nothing.
   Every prompt field says "Null if none was stated"
   (`app/prompts/call_insights_*.py`), so a null here is a finding,
   not a gap, and it will never change unless the call is regraded.
   Fields: pain or need, timeline, budget, competition, decision-maker
   detail, reason to call back, opportunity size / details, meeting
   attendees, case study (`case_study_provided = false`), agreement
   `none`.
2. **Not generated yet (coming).** Transcript queued or transcribing;
   insight row exists with `status = pending` (sweeper runs every
   minute); a Salesforce reviewer has not scored the appointment yet.
   Scorecards (Pipeline grade, DARTS) are matched by contact with
   `Appt_Date__c` inside 60 days after the call
   (`SCORECARD_WINDOW_DAYS`), and a card is only scored after the
   meeting happens, so for a recent call "not yet" is literally true.
3. **Never coming (structural).** No recording → no transcript → no
   summary, highlights or insights. Call under `MIN_CALL_SECONDS` (20)
   or `MIN_TURNS` (4) → `not_applicable / too_short`. Rep talk share
   when the stored transcript JSON has no utterance `end` offsets
   (transcribed before 2026-09-15; see transcription/NOTES.md). Pitch
   calls, confirmations, follow-ups and reschedules have no briefing
   (`sp_appointment_email` is matched by `sf_task_sf_id`, and a
   briefing is generated for the booking call only), so no meeting
   date, meeting type, building or briefing link. Adherence exists only
   for calls placed through a Talk Track session, and **Talk Tracks are
   on hold as of 2026-10-06** (client asked to archive all; redesign
   later), so no new adherence rows are coming at all.
4. **Blank in Salesforce.** Title, company, sub-industry, contact
   mailing address (building fallback), task with no contact or lead.
   Rare and not ours to fill.
5. **Pending forever (bug).** `_select_work` in
   `app/commands/call_insights_generate.py` filters out calls under
   20 s, so they never get an insight row; the client's
   `insightsState()` reads "transcribed with no insight row" as
   `pending`. Those calls show "Pending" with no path out: 404 on
   PROD (267 pitch, 137 appointment). A further 26 transcribed calls
   over 20 s have no row, which is sweeper lag.

## PROD counts (reader replica, 2026-10-06)

Default view is "This month" (October 2026), which is what the client
looks at. Population = Hub population since 2026-01-01.

| View | Calls | No recording | Transcribed | Insights completed | Briefing matched | Grade with stars | DARTS scored | Adherence scored |
|---|---|---|---|---|---|---|---|---|
| Appointments, this month | 934 | 129 | 804 | 772 | 578 | 0 | 69 | — |
| Appointments, all time | 36,618 | 7,116 | 5,721 | 5,548 | 5,303 | 23,694 | 6,719 | — |
| Pitches, this month | 3,102 | 44 | 3,058 | 2,986 | 6 | — | — | 0 |
| Pitches, all time | 137,187 | 2,340 | 37,966 | 37,463 | 83 | — | — | 7,243 since 08-25 |

Observations behind the vocabulary:

- All-time, 84 % of appointment calls have no transcript (the poller
  went live 2026-08-25), so on a wide date range almost every AI cell
  is empty for a structural reason, not a model one.
- This month, a Pipeline card exists for 618 of 934 appointment calls
  but **none has stars yet**: stars are filled after the meeting. Older
  than 60 days: 17,732 of 27,418 have stars (65 %), the rest never
  will. DARTS is scored on 19–25 % of cards even when old. The "yet"
  must depend on the call's age.
- Briefing match by sub-kind since 2026-09-01: bookings 3,585 of
  5,080 (71 %), confirmations 52 of 564, follow-ups 118 of 576. So
  "no briefing" on a confirmation is expected; on a booking it is a
  gap in the email pipeline (29 %) that this spec does not try to
  explain in the cell.
- Talk share null by month of call: 100 % through August, 60 % in
  September, 0 % in October. `transcribed_at` before 2026-09-15 is a
  reliable client-side rule.
- Adherence: last completed row 2026-09-25; nothing since. Consistent
  with Talk Tracks being on hold.

Null share among completed insights (the class-1 cells, all time):

| Field | Appointments (n = 7,251) | Pitches (n = 43,623) |
|---|---|---|
| Budget | 84 % | — |
| Competition | 61 % | 54 % |
| Pain or need | 41 % | — |
| Timeline | 41 % | — |
| Decision maker `unconfirmed` | 49 % | — |
| Decision-maker detail | 19 % | — |
| Meeting format `unclear` | 11 % | — |
| Meeting attendees empty | 9 % | — |
| Opportunity size / details empty | 36 % | 90 % |
| Agreement `none` | 5 % | — |
| Reason to call back | — | 39 % |
| Case study not provided | — | 99.7 % |
| Opportunity heat 0 ("not relevant") | — | 5 % |
| Objection handling `none` (no objections) | — | 7 % |
| Close attempts = 0 | 12 % | 61 % |

These are the cells the client is staring at. "Not stated" on 84 % of
Budget cells is the honest answer; "n/a" would hide that the model did
look.

## Vocabulary

Rules: no "—" anywhere on the page. A cell shows short muted text
(sentence case, no trailing period) and a tooltip with the long
reason. **"N/A" means the column cannot apply to this row** by its
kind or era (a briefing on a confirmation call, talk share on a
transcript made before timings were stored); the tooltip says why.
Everywhere else the cell says what is missing, because "N/A" would
collapse "the call had nothing" with "we could never look", which is
the distinction the client is asking for (decided with Tomas
2026-10-06). "Not detected" is acceptable for class 1 only, but "Not
stated" is truer to the prompt (the model reports what the prospect
said, it does not detect).

| Cell | Cause | Cell text | Tooltip |
|---|---|---|---|
| Any AI column | `transcript_state = no_recording` | No recording | This call has no recording, so there is no transcript to read |
| Any AI column | `transcript_state` pending / transcribing | Transcribing… | Insights follow a minute after the transcript |
| Any AI column | `transcript_state = failed` | No transcript | Transcription failed for this call |
| Any AI column | insight row `pending`, or no row and call ≥ 20 s | Pending | Insights are generated shortly after transcription (keep) |
| Any AI column | `not_applicable / too_short` | Call too short | Calls under 20 seconds or 4 turns are not analysed |
| Any AI column | `not_applicable / no_transcript` | No transcript | The transcript could not be read |
| Any AI column | `failed` | Unavailable | Insight generation failed for this call (keep) |
| Reason for meeting | never null when completed | — | — |
| Pain or need, Timeline, Budget | model null | Not stated | The prospect did not state one on the call |
| Decision maker `unconfirmed` | already labelled | Unconfirmed (keep) | The call never established who decides |
| Decision-maker detail (sub-line) | model null | *(omit the sub-line)* | — |
| Competition | model null | None named | The prospect named no current vendor |
| Reason to call back | model null | Nothing to build on | The call gave nothing to leverage on a callback |
| Opportunity size / details | empty list | Not stated | The prospect stated no sizing facts |
| Meeting with | empty list | Not stated | The call did not say who will attend (keep) |
| Case study | `case_study_provided = false` | Not provided (keep) | The rep gave no case study or differentiator |
| Agreement to meet `none` | already labelled | No acceptance (keep) | — |
| Meeting format `unclear` | already labelled | Unclear (keep, flyout only) | — |
| Urgency, Heat, Objection handling, Agreement | null on a completed row (should not happen; schema requires them) | Not graded | — |
| Flags | empty | No flags (keep) | — |
| Rep talk share | completed row, `rep_talk_share_pct` null, `transcribed_at` < 2026-09-15 | N/A | Talk time is measured for calls transcribed from September 2026 on |
| Opportunity grade | no starred card, call within the 60-day review window | Not scored yet | The reviewer scores the Pipeline card after the meeting |
| Opportunity grade | no starred card, window closed | Not scored | No scored Pipeline card was found for this appointment |
| DARTS gate | same split | Not scored yet / Not scored | same pattern |
| Talk track adherence | always, while Talk Tracks are on hold | *(hide the column; see below; "N/A" if kept)* | Talk Tracks are being redesigned |
| Meeting date, Meeting type, Briefing | no briefing, `kind` is confirmation / follow_up / reschedule | N/A | Briefings belong to the booking call |
| Meeting date, Meeting type, Briefing | no briefing, `kind` is booking | No briefing | No appointment briefing is matched to this call |
| Meeting date | briefing, `appt_scheduled_at` null | Not set | The briefing has no meeting time |
| Meeting type | briefing, type null | Not set in Salesforce | Phone / In Person was blank when the appointment was booked |
| Building | no briefing, non-booking kind, no contact address | N/A | Briefings belong to the booking call, and the contact has no street address |
| Building | no address on briefing or contact (booking) | No address | Neither the briefing nor the contact record has a street address |
| Building | image fetch failed | *(keep the map-off icon)* | No satellite imagery for this address |
| Company, Title | blank in Salesforce | Not in Salesforce | The contact record has no title / company |
| Prospect | task has no contact or lead | No contact | The Salesforce task is not linked to a contact |
| Account owner (sub-line) | null | *(omit)* | — |
| Callback | no later call | No callback yet (keep) | No later call has been logged on this contact |
| Summary column | not generated | *(follow transcript state: No recording / Transcribing… / Pending)* | — |
| Duration | null | Unknown | Salesforce logged no duration |

Flyout: the definition lists under Insights use the same words; the
section-level empty states (summary, highlights, meeting, building,
transcript, recording) already explain themselves and stay as they
are. "Not stated on the call" under Meeting with becomes "Not stated"
for consistency.

Export (`export_header` writes "—" for a missing header value): leave
as is; a text file header is not the client's complaint. Revisit if
they ask.

### Talk track adherence column

Talk Tracks are on hold (client, 2026-10-06: archive all, redesign
later). The Pitches view's "Talk track adherence" column therefore has
no present or future source: the sweeper's last row is from
2026-09-25 and nothing new can appear. Recommendation: **hide the
column and its sort** (`PITCH_COLUMNS`, `SORT_FIELDS`) until the
redesign lands, rather than show "Not scored" on every row. The
server field `adherence_pct` and the sort key stay; nothing to
migrate. Alternative if the client wants the slot visible: "On hold"
with the tooltip "Talk Tracks are being redesigned".

## Server changes

Everything in the vocabulary table is derivable from fields the item
already carries (`transcript_state`, `insights.status`,
`insights.not_applicable_reason`, `transcribed_at`, `appointment_email`,
`building`, `contact`, `called_at`) except two things. Both are
sweeper or Python-side; **no SQL change, no migration, no regrade**.

1. **Short calls get a `too_short` row** (fixes class 5). In
   `_select_work` drop the `CallDurationInSeconds >= MIN_CALL_SECONDS`
   filter from `new_filters`; in `generate_insight` check the task
   duration before `load_utterances` (today only the turn count is
   checked, after the S3 read) and mark `too_short`. No model call is
   made for them. The 404 existing calls are picked up as "new" on the
   next sweep because the selection is `~exists(CallInsight …)`; no
   backfill script.
2. **Stop claiming conversation-bucket calls.** `_select_work` takes
   every disposition in `CALL_DISPOSITION_MAP`, including Gatekeeper,
   Contact and Left Live Message, which `kind_for()` then rejects as
   `unsupported_disposition`: 610 wasted rows on PROD, about 300 a
   month. Restrict the list to the appointment and pitch buckets. Same
   function as change 1.
3. **`review_window_open` on the item**: `called_at + SCORECARD_WINDOW_DAYS > now`
   computed in `_build_item`, so the client does not copy the 60-day
   constant. One line, one schema field.

Rejected alternative: have the API synthesize
`insights = {status: not_applicable, reason: too_short}` for transcribed
calls under 20 s in `_build_item`. It avoids 400 rows but puts the
threshold in two places (sweeper and API) and leaves the sweeper's
own `MIN_TURNS` path producing real rows for the same reason. One
source of truth is the sweeper.

Minor, optional: `status = failed` with `generation_attempts < 5` is
still going to be retried, but the client shows "Unavailable". Exposing
`generation_attempts` (or mapping to `pending` server-side until the
ceiling) would let the cell say "Pending" until the row is really
dead. 44 rows on PROD; not worth a field unless the client notices.

## Client changes

- `INSIGHT_PLACEHOLDERS` grows by transcript-state and
  not-applicable-reason entries; `insightsState()` returns the reason
  (`too_short`, `no_transcript`) instead of a bare `not_applicable`.
- `AppointmentCallInsightCell.vue`: per-field empty text from a small
  map (`EMPTY_TEXT[field]`), replacing the seven inline `—` spans.
- `AppointmentCallsPage.vue`: grade and DARTS use
  `row.review_window_open`; meeting, building, briefing, company,
  title, prospect cells take the words above; `PITCH_COLUMNS` loses
  `adherence`.
- `AppointmentCallFlyout.vue`: same map for the Insights definition
  list; "Not stated on the call" → "Not stated".
- `formatDuration` / `formatMeetingTime` return "Unknown" / "Not set"
  instead of "—".
- Tests: `tests/` for the constants module (placeholder resolution per
  state) and a render test per cell class.

## Implemented (2026-10-06)

Server (`udab-server`, branch `hub-empty-cells`):

- `_select_work`: appointment + pitch dispositions only; the 20 s
  duration filter is gone. Null-duration calls are now selected too
  (they were excluded by the old `coalesce(…, 0)`); `MIN_TURNS` still
  guards them.
- `generate_insight`: duration under `MIN_CALL_SECONDS` →
  `not_applicable / too_short` before the S3 read. The 404 stuck PROD
  calls self-heal on the first sweep after deploy.
- `AppointmentCallItem.review_window_open` from `_build_item`
  (`called_at + SCORECARD_WINDOW_DAYS > now`, UTC-naive both sides).
- Tests: selection of any duration and exclusion of the conversation
  bucket; too-short path makes no S3 read and no model call;
  `review_window_open` for 1-day and 61-day-old calls.

Client (`udab-client`, branch `hub-empty-cells`):

- `insightsState()` → `no_recording | transcribing | transcript_failed
  | pending | too_short | no_transcript | failed | not_applicable |
  ready`; `INSIGHT_PLACEHOLDERS` carries the words; `EMPTY_TEXT` the
  per-field "model found nothing" words; helpers `insightPlaceholder`,
  `summaryPlaceholder`, `talkShareEmpty`, `briefingEmpty`,
  `meetingTimeEmpty`, `meetingTypeEmpty`, `buildingEmpty`,
  `scorecardEmpty`. `formatDuration` → "Unknown", `formatMeetingTime`
  → "Not set"; `kindLabel` / `transcriptStateLabel` → "Unknown".
- Adherence column and sort removed from the Pitches view
  (`PITCH_COLUMNS`, `SORT_FIELDS`); a stored `sort: adherence` falls
  back to the default. Server sort key untouched.
- Cell, page and flyout use the map; tests cover every state, each
  helper, and that no cell renders a dash.

Judgment calls not in the vocabulary table:

- Other `not_applicable` reasons (e.g. `unsupported_disposition`) →
  "Not analysed"; those rows are outside the Hub population anyway.
- Talk share null on a transcript made on or after 2026-09-15 →
  "Not measured" (not "N/A"): timings were expected and are missing.
- Coaching note null → "No coaching note"; reason for meeting null →
  "Not stated"; graded enums null on a completed row → "Not graded".
- Summary column: transcribed without a summary → "Pending" (was
  "generating…"); otherwise the transcript-state words.
- Pitch rows count as non-booking for the briefing columns (N/A).
- `formatBytes` keeps "—"; it only feeds the "Download ready" button.

## Open questions

- Does the client want "Not stated" or "Not detected" for class 1?
  "Not detected" matches their words; "Not stated" matches what the
  model actually reports. Either is one string in the map. ANSWER: use the more precise term, whichever it is
- Hide the adherence column, or keep it with "On hold"? - ANSWER: leave as-is
- "No briefing" means two things. On a confirmation, follow-up or
  reschedule call it is the normal case: the briefing belongs to the
  booking call, a different row. On a booking call a briefing was
  expected, and 29 % of bookings since September have none (an
  appointment-email pipeline gap, cause not yet investigated).
  Decided 2026-10-06: split by `row.kind`; non-booking rows say
  "N/A" (tooltip "Briefings belong to the booking call"), booking rows
  say "No briefing". The 29 % itself is raised separately. ANSWER: keep
