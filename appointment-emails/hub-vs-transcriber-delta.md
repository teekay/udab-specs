---
kind: spec
status: draft
area: appointment-emails
repos: [udab-server, udab-client]
updated: 2026-10-08
summary: "What the auto-transcriber transcribes that the Hub never shows: three classes measured on PROD, client questions."
---

# Account Management Hub vs auto-transcriber — the population delta

Status: DRAFT 2026-10-08 — measured on the PROD reader (`sapper`
schema), nothing decided, nothing built. For the client when she is
back. Builds on [call-queue.md](call-queue.md) (the Hub population) and
the transcription area's `NOTES.md` (the poller's scope); overlaps
[call-queue-sales-enablement.md](call-queue-sales-enablement.md), which
proposed a tab for one of the three classes below before the poller
started transcribing those calls.

## Why this exists

Two rules decide "which calls" and they were written at different
times by different people:

- **The Hub** (`app/services/appointment_calls.py`,
  `population_filters()`): appointment + pitch dispositions,
  `TaskSubtype = 'Call'`, not deleted, created on or after
  2026-01-01, inner join to `sf_account` with `RecordTypeId =
  Pipeline Client` **and** `Status__c = 'Active'`. The account rule is
  evaluated **at view time**.
- **The poller** (`auto-transcribe`, `app/commands/auto_transcribe.py`,
  since udab-server #774 on 2026-09-26): appointment + pitch +
  **conversation** dispositions (Gatekeeper, Contact, Left Live
  Message), recording URL present, created in the last 24 h, and
  **either** the same account rule **or** the Task owner holds a
  sales-enablement role (`SDR`, `Director of Sr SDR`; ~28 users).
  Conversation calls use the owner rule only. The account rule is
  evaluated **at tick time**, against Salesforce live.

The Hub therefore shows a strict subset of what gets transcribed (for
calls with a recording), and the transcribed-but-invisible remainder is
what the client may or may not want to see.

## What PROD says (2026-10-08, last 14 days)

Cross-tabulation of every non-deleted Task with a disposition in either
rule, created 2026-09-24 17:20 → 2026-10-08 17:20 UTC, against
`sp_transcription_job_task`. Owner list taken from the latest scheduled
job's `filters_json.eligible_task_owner_ids`.

**Hub → transcriber: no gap.** Every Hub row with a recording was
transcribed (11,107 appointment + pitch). The 547 Hub rows without a
recording show "No recording", which is a vendor fact, not a miss.
Zero Hub rows are stuck in "Pending" past the poller's 24 h window.

**Correction 2026-10-20 (PROD replica, same method):** the "no gap"
claim above is wrong for CloudCall confirmation and follow-up calls.
Their recording URL is stamped days to weeks after the call (median
≈ 3 days; `URL_Expiry_Time__c` set, so not our stamper), outside the
poller's 24 h `CreatedDate` window, and nothing re-scans: since
2026-09-01, 1,252 such Hub rows have a URL and no job row (648
Appointment Confirmation, 594 Appointment Follow-up; ≈ 40 per working
day), and only ≈ 7 % of that class is transcribed at all. The Hub
shows them as "Transcribing…" indefinitely (see the pre-rollout note
in NOTES.md, same symptom). Needs a re-scan rule, not a wording fix.

**Transcriber → Hub: ~2,000 transcribed calls the Hub never shows.**

| Class | 14-day count | Why the Hub hides it |
|---|---|---|
| A. Conversation calls | 1,183 | Hub only has appointment / pitch kinds. These exist for Engage adherence (#774). |
| B. Appointment / pitch by sales-enablement owners on non-Active accounts or bare Leads | 413 (132 appt, 281 pitch) | Hub requires an Active Pipeline account and inner-joins `sf_account`. This is the population [call-queue-sales-enablement.md](call-queue-sales-enablement.md) proposed a tab for. |
| C. Appointment / pitch whose account is no longer Active Pipeline, or has no account | 384 | Account drift and manual POSTs (breakdown below). |

Class C splits by how the transcript came to exist:

| Source | Account now | Tasks |
|---|---|---|
| scheduled poller | Pipeline, `Canceled` | 96 |
| scheduled poller | Pipeline, `On Hold` | 56 |
| manual POST | non-Pipeline record type, `Active` | 187 |
| manual POST | no account (bare Lead) | 24 |
| manual POST | Pipeline, `Prospect` / `Canceled` / `On Hold` | 20 |
| manual POST | non-Pipeline, `Prospect` | 1 |

So 152 calls were transcribed while the client was Active and then
**left the Hub** when the account went Canceled / On Hold. The other
232 were never in either scope; someone asked for them by API.

**The reverse drift** is possible in principle and was zero in this
window: a Task whose account becomes Active more than 24 h after the
call would appear in the Hub as "Pending" and never be picked up
(the poller's `CreatedDate` window has passed; nothing re-scans).

**Not a factor:** `TaskSubtype = 'Call'` excluded nothing (every Task in
scope is a Call); the 2026-01-01 floor is older than any transcript.

Every count is from the hourly `sf_task` mirror; a URL stamped in the
last hour is not visible. (Method: the Tasks-vs-outcome query recipe is
in `../transcription/NOTES.md`, "Health check 2026-10-08".)

## What the delta costs

Deepgram minutes are already spent either way. The extra cost is
downstream: the call-insights sweeper (`call-insights-generate`,
`_select_work`) selects every `sp_call_transcript` whose Task has an
appointment or pitch disposition — the #796 fix narrowed it to the
Hub's dispositions, **not** to the Hub's account rule. So classes B and
C (≈ 800 transcripts in 14 days, ≈ 1,700 / month) get Bedrock
insights that nobody can see. Class A is excluded by disposition.
Verified in code 2026-10-08; not yet counted on PROD.

## Questions for the client

1. **Class B (sales-enablement reps).** Their calls are now transcribed
   automatically. Should they be visible, and if so on the existing
   tabs ("put them under Reps", the original ask) or on their own tab
   (the draft spec's proposal)? The only blocker from 2026-09-24 was
   "what is an SE Appointment" (Q4 in that spec).
2. **Class A (conversation calls).** These were transcribed for Engage
   adherence scoring, not for the Hub. Does the Hub want a third kind
   bucket, or do they stay out? (Volume caution: 34,077 conversation
   calls with recordings on Active Pipeline accounts in 14 days are
   *not* transcribed today; showing the 1,183 SE ones invites "where
   are the rest", and transcribing the rest is ≈ 30× the current
   conversation spend.)
3. **Class C (account drift).** When a client goes Canceled / On Hold,
   should its already-transcribed calls stay visible (account status
   "as of the call") or disappear (status as of now, today's
   behaviour)? Same question for a Prospect that later becomes Active:
   should its earlier calls be transcribed retroactively?
4. **Manual POSTs** for non-Pipeline accounts (187 in 14 days). Who is
   making them and do they expect to find the result in the Hub?

## Options (not recommendations yet)

- **Keep as is** and document: the Hub is "Active Pipeline clients'
  appointment and pitch calls", the transcriber is wider by design.
  Zero work.
- **Snapshot the account rule at transcription time.** Store
  `account_sf_id` / account status on `sp_call_transcript` (or a
  per-Task "hub eligible" flag written by the poller) and let the Hub
  OR it with the live rule. Fixes class C both ways, lets "as of the
  call" be the answer. Medium: migration, poller write, Hub query.
- **Build the Sales Enablement tab** from the draft spec, re-based on
  the poller's owner-role rule instead of the `Department` rule it
  proposed (the poller already resolves the roster from Salesforce
  roles; the Hub should read the same list). Medium–large.
- **Conversation kind in the Hub.** Only if the client asks; needs its
  own columns since none of the briefing / scorecard columns apply.

## Decided

Nothing yet.
