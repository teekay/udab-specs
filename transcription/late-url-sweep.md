---
kind: spec
status: in-progress
area: transcription
updated: 2026-10-20
repos: [udab-server, udab-client]
summary: "URLs arriving after the 24 h poller window: daily 45-day sweep, retry cap, queued / not-transcribed Hub states."
---

# Late recording URLs — daily sweep and honest Hub states

Status: DRAFT 2026-10-20. Measured on the PROD replica; nothing built.
Raised by the client on 2026-10-20 (Hub rows showing "Transcribing…"
that never will be). Builds on `auto.md` (the poller) and
`../appointment-emails/hub-empty-cells.md` (the Hub vocabulary).

## Problem

The poller selects Tasks by `CreatedDate >= now − 24 h` (PROD confirmed:
`filters_json.created_since` is exactly `created_at − 24 h`). A Task whose
`Call_Recording_URL_Public__c` is written later than that is never looked
at again. Nothing else re-scans.

PROD, Hub population (Active Pipeline accounts, appointment + pitch
dispositions), created 2026-09-01 → 2026-10-16, URL present, **no
`sp_transcription_job_task` row at all**:

| Disposition | Stuck | Of all such calls with a CloudCall session id |
|---|---|---|
| Appointment Confirmation | 648 | 703 (47 transcribed, 7 %) |
| Appointment Follow-up | 594 | 653 (57 transcribed, 9 %) |
| Appointment / KDM Pitched / other spellings | 10 | — |

≈ 40 per working day. All CloudCall; Orum URLs are present at creation.
Deepgram cost to drain the backlog: 164,138 call-seconds ≈ **$24**.

How late the URL arrives (stamp time taken as `URL_Expiry_Time__c − 30 d`,
CloudCall mints 30-day tokens): for Appointment and KDM Pitched calls
the URL is there within a day (1,046 of 1,119 and 3,160 of ~8,000 first
stamps); for Confirmation and Follow-up calls **one** of 1,356 got a URL
within a day; the rest got one 4 to 45 days later. 1,243 of the 1,252
stuck rows were stamped in a single hour, 2026-10-19 18:00 UTC, with
`URL_Expiry_Time__c` set. Nothing in udab-server writes that field
(`stamp-cloudcall-urls` PATCHes the URL only), and its lookback is 120
minutes, so this is the client's Salesforce-side CloudCall refresher
(the "backstop batch" in NOTES.md) running in bulk. The same run
re-stamped 4,840 already-transcribed KDM Pitched calls 14–30 days after
the call, which is why `LastModifiedDate` is not a usable stamp time.

Why our own stamper misses confirmation and follow-up calls is **not
explained by its inputs**: they carry `synety__Call_Session_Id__c` and
`synety__Actual_Date_Time_of_Call__c` like every other call, and are
created within minutes of the call. The stamper's match (SessionID +
exactly one `Leg == 1` with `CallRecordingAvailable`) must be failing for
how these calls are placed; see Open questions. The sweep below does not
depend on the answer.

What the Hub shows for these rows today: "No recording" for a few days,
then "Transcribing…" forever (`transcript_state = pending`, which
`insightsState()` folds into `transcribing`). The flyout says "pending /
The call is queued for transcription". Both are false.

The same display problem covers every pre-rollout call with a URL:
≈ 110,000 Hub rows created 2026-01-01 → 2026-08-24 (72,000 Orum,
26,000 CloudCall). There is **no cutoff date** to word around: per-month
coverage from account-scoped manual backfills is 9 % (January) to 48 %
(August), and thousands more carry `failed` rows from expired URLs.
`APPOINTMENT_CALLS_SINCE = 2026-01-01` is the Hub's population floor,
not a transcription boundary.

## Decided facts the design rests on

- "Never queued" is knowable: every selection writes a
  `sp_transcription_job_task` row (`pending` first), scheduled ticks
  included (`record_skips=False` only suppresses `already_transcribed`
  copies). No row = never selected.
- "Will it be queued" is a pure function of the Task and the clock: the
  poller's scope is the Hub's scope plus the Sales Enablement owner
  alternative, so for a Hub row the only moving part is the time window.
  A Hub row with a URL, no job row, and `CreatedDate` inside the window
  will be picked up; outside it, it will not, until someone requests it.
- `auto-transcribe --backfill` already does the hard part: pages a
  `CreatedDate` window by Id (`after_id`, 200 per page), refreshes
  expired CloudCall URLs in memory, excludes claimed Tasks
  (`exclude_claimed=True`), writes no skip rows, waits for each chunk's
  worker, and keeps the owner alternative. `--lookback-hours` applies to
  it when `--since` is absent.

## Design

### 1. Daily sweep (udab-server, infra) — the fix

Schedule a second Batch job, once a day off-peak (e.g. 09:00 UTC, before
US calling hours):

```
auto-transcribe --backfill --lookback-hours 1080 --call-result appointment --call-result pitch
```

- **Window 45 days** (`1080 h`): covers the observed stamp delays (up to
  45 days) with margin; `_refresh_cloudcall_urls` re-mints any URL that
  has expired meanwhile (CloudCall keeps recordings a year). The first
  run also drains the September backlog.
- **Appointment + pitch only**: the Hub's kinds. Conversation calls are
  transcribed for adherence, not shown in the Hub, and would triple the
  SOQL volume for nothing.
- **Size on PROD**: 28,176 Hub rows with a URL in the last 35 days, of
  which 27,329 already have a job row; so ≈ 150 pages of 200 per run,
  each one SOQL + one claimed-exclusion select, and a handful of small
  jobs. Minutes of wall time, ≈ $1/day of Deepgram once the backlog is
  gone.
- **Coexistence with the per-minute tick**: `identical_batch_job_active()`
  keys on the container command, so the two never block each other as
  "identical". The sweep waits for the scheduled worker before every
  page (`_scheduled_worker_busy`), and the tick skips while a sweep chunk
  runs, so only one worker is ever transcribing. A Task can still be
  claimed by both within the same minute: one duplicate Deepgram call,
  never a wrong transcript (existing gotcha).
- **Scope side effect, intended**: Tasks whose account became Active
  more than 24 h after the call ("reverse drift", class C in
  `../appointment-emails/hub-vs-transcriber-delta.md`) are now
  transcribed if they are under 45 days old.

Code change needed, small: **cap retries**. `claimed_sf_task_ids` treats a
`failed` row as claimed only for `FAILED_RETRY_AFTER_HOURS = 6`; a daily
sweep would retry every dead Orum recording (400 at ~10 days) every day
for 45 days. Add "failed ≥ `MAX_FAILED_ATTEMPTS = 3` times" to the
claimed predicate (count of `failed` rows per `sf_task_sf_id`). That also
bounds the per-minute tick, which today can retry four times inside its
24 h window. Expose nothing; a constant in `transcription_jobs.py`.

Rejected: a per-tick `LastModifiedDate >= now − 24 h` selector. It
catches a late URL within a minute instead of within a day, but
`LastModifiedDate` moves on every edit and on the client's bulk
re-stamps (6,000+ Tasks in one hour on 2026-10-19), so a tick would
sweep thousands of claimed rows against the 10,000-row SOQL guard. The
daily sweep is zero selection code and the Hub promise becomes "within a
day of the recording arriving", which is enough.

### 2. Honest Hub states (udab-server `appointment_calls.py`, udab-client)

**Do not rename or add `transcript_state` values.** The client's
`insightsState()` sends any unknown state to its default branch, which
renders "No recording"; a server-first deploy would mislabel every
affected row. Instead the item gains one boolean, on the
`review_window_open` precedent:

- `transcription_window_open = called_at >= now − TRANSCRIPTION_LOOKBACK`
  (UTC-naive both sides), where `TRANSCRIPTION_LOOKBACK` is one constant
  in `transcription_jobs.py` that also feeds the poller's
  `--lookback-hours` default (24 h today; the sweep PR raises what the
  Hub reads to 45 days). Not in the SQL sort twin; sorting by state
  stays on the five values.

The client then reads `pending` + the flag:

| Condition | Cell text (AI columns, Summary) | Tooltip | Flyout badge / text |
|---|---|---|---|
| `pending`, window open | Queued | Transcribed within a day of the recording arriving | queued / The call will be transcribed within a day |
| `pending`, window closed | Not transcribed | Only recent calls are transcribed automatically; older calls are transcribed on request | not transcribed / Calls older than the automatic window are transcribed only on request |
| `transcribing` | Transcribing… (unchanged) | | |

- "Not yet retroactively transcribed" (the client's phrase) is avoided
  on purpose: it promises a backfill that is out of scope here.
- Not a date rule: the pre-August Orum example the client sent and a
  CloudCall call from June read the same, because they are the same.

### 3. Stamper diagnosis (udab-server, separate, not blocking)

Run `stamp-cloudcall-urls --dry-run` style logging for one working day
and count, for confirmation / follow-up Tasks in the window, which
branch loses them: no listing record for the SessionID, no `Leg == 1`,
several `Leg == 1`, or `CallRecordingAvailable` false. The answer decides
whether `cloudcall-url-heal.md` Part B is the fix or whether these calls
are placed through a path CloudCall lists differently.

## Tests

- `claimed_sf_task_ids`: three `failed` rows → claimed regardless of age;
  two → not claimed once older than 6 h.
- `transcript_state()` / SQL twin: the two new branches at the window
  boundary (44 d 23 h vs 45 d 1 h), Python and SQL agree on a seeded page.
- Client: placeholder resolution for `queued` and `not_transcribed` in
  cell, Summary and flyout; the badge list; no cell renders a dash.

## PRs — small, in this order

1. **udab-server: `transcription_window_open`.** Constant, one field in
   `_build_item` + schema, test at the boundary. No visible change.
2. **udab-client: Queued / Not transcribed.** `insightsState()` splits
   `pending` on the flag; placeholders, flyout, `TRANSCRIPT_STATES`
   wording; tests per state. Ships after 1 is on PROD.
3. **udab-server: retry cap** in `claimed_sf_task_ids`
   (`MAX_FAILED_ATTEMPTS = 3`), test.
4. **udab-server + infra: the daily sweep.** Raise the Hub's window
   constant to 45 days in the same PR as the EventBridge rule goes live,
   so "Queued" never promises more than the sweep delivers. First run
   drains ≈ 1,250 calls (≈ $24); verify next day with the stuck-count
   query in NOTES.md.
5. Stamper diagnosis (§3), separately.

Then tell the client: Confirmation / Follow-up calls transcribe within a
day of their recording arriving; "Not transcribed" means older than the
automatic window and available on request.

**Out of scope (decided 2026-10-20):** any backfill of the pre-rollout
backlog. The Hub says "on request"; a request is an API POST or a
manual `--backfill --since`.

## Implemented

- PR 1 (udab-server, branch `transcription-window-open`, 2026-10-20):
  `SCHEDULED_LOOKBACK_HOURS = 24` in `transcription_jobs.py` feeds both
  the poller's `--lookback-hours` default and the Hub item's
  `transcription_window_open` (computed in `_build_item`, no SQL, no
  migration). Test at ±1 h of the boundary. Tests green locally.
- PR 2 (udab-client, branch `transcription-window-open`, 2026-10-20):
  `transcriptPhase(row)` maps `pending` + the flag to `queued` /
  `not_transcribed`; `insightsState`, `summaryPlaceholder`, the flyout
  badge and empty texts use it. Words: "Queued" / "Not transcribed" (cells),
  "will be transcribed within a day" / "not transcribed automatically …
  on request" (flyout). Tests per state in constants, cell and flyout.
  Ships with PR 1 (server first; a missing flag reads as not transcribed).

## Open questions

- Window length: 45 days covers what PROD showed; the client's refresher
  cadence is unknown. If it stamps older than that, raise the window
  (cost is SOQL pages, linear).
- Stamper: why confirmation / follow-up calls never match (§3).
