---
kind: spec
status: in-progress
area: transcription
updated: 2026-10-21
repos: [udab-server, udab-client]
summary: "URLs arriving after the 24 h poller window: LastModifiedDate tick + 45 d floor, retry cap, honest Hub states."
---

# Late recording URLs — react to the stamp, and honest Hub states

Status: IN PROGRESS. PRs 1–3 merged; PR 4 built 2026-10-21, unmerged.
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

### 1. Select on LastModifiedDate (udab-server) — the fix

Decided 2026-10-21 (Tomas): the daily sweep is the wrong tool; the
poller should key on the event it is missing. Salesforce bumps
`LastModifiedDate` on every write to the Task, including the URL stamp
by our stamper, the client's SF batch and the client's bulk refresher.
So the scheduled tick selects

```
LastModifiedDate >= now − 24 h AND CreatedDate >= now − 45 d AND <unchanged scope>
```

instead of `CreatedDate >= now − 24 h`.

- **Superset of today's selection** (`LastModifiedDate >= CreatedDate`),
  so nothing the tick catches now is lost. A URL that lands days later
  is picked up within a minute of landing, not within a day.
- **The CreatedDate floor is what keeps the pre-rollout backlog out.**
  Measured on the replica (mirrored `LastModifiedDate`, Hub scope, URL
  present): the refresher's bulk days re-stamp tens of thousands of
  Tasks, most of them pre-rollout with no job row (2026-10-03: 21,021
  modified, 16,519 without a row; 2026-10-10: 17,653 / 12,838;
  2026-10-17: 34,715 / 29,026). Without the floor the tick would start
  transcribing the backlog the next time the refresher runs. 45 days
  matches the observed stamp delays for Confirmation / Follow-up calls
  (4–45 days); the constant lives in `transcription_jobs.py` and the Hub
  reads it.
- **Volume per tick**: ordinary days 200–3,300 modified Tasks in scope,
  nearly all freshly created (10-14: 1,208 of which 1,190 created that
  day; 10-21: 233 / 219), so one SOQL page as today. Bulk days: up to
  ~35,000 modified, of which the post-rollout share (≈ 5,000–7,000)
  passes the floor and is almost entirely claimed; the claimed-exclusion
  select handles 1,000 ids per query. Busiest measured hour: 8,885.
- **The 10,000-row guard** (`MAX_SOQL_ROWS`, newest-first, overflow
  dropped with a loud log) is the one risk: a bulk day with more than
  10,000 post-rollout re-stamps inside the 24 h window would hide the
  oldest for up to a day and then lose them. Measured bulk days stay
  under it (≈ 7,000 on 2026-10-17). Mitigation if it ever trips: raise
  the guard (SOQL pages 2,000 per API call) or page by Id as the
  backfill does. Watch the log on the first refresher run after deploy.
- **Backfill mode unchanged**: manual `--backfill --since/--until`
  keeps its CreatedDate window and Id paging.
- **Hub**: `transcription_window_open` becomes "mirrored
  `LastModifiedDate` within 24 h AND `called_at` within 45 d", so a
  re-stamped call flips to Queued and is transcribed within a minute.
- **Retry cap (PR 3) still needed**: every re-stamp of a dead-URL Task
  re-triggers it after the 6 h cooldown.
- **The September backlog**: the 1,252 stuck calls were re-stamped on
  2026-10-19, so by deploy they are outside the 24 h window. They need
  one manual `--backfill --since 2026-09-01 --call-result appointment
  --call-result pitch` (≈ $24) or stay "Not transcribed". Tomas's call;
  no code either way.

Rejected (2026-10-20, revised 2026-10-21): the daily 45-day
`--backfill` sweep. It re-scans the whole window once a day instead of
reacting to the URL, needs its own EventBridge rule and Batch timeout,
and promises "within a day" where the tick promises "within a minute".
The earlier objection to a `LastModifiedDate` selector (bulk re-stamps
sweeping thousands of claimed rows) was measured today and is
affordable: one SOQL of a few thousand rows per tick on bulk days,
nearly all claimed, and bounded by the CreatedDate floor.

### 2. Honest Hub states (udab-server `appointment_calls.py`, udab-client)

**Do not rename or add `transcript_state` values.** The client's
`insightsState()` sends any unknown state to its default branch, which
renders "No recording"; a server-first deploy would mislabel every
affected row. Instead the item gains one boolean, on the
`review_window_open` precedent:

- `transcription_window_open = called_at >= now − TRANSCRIPTION_LOOKBACK`
  (UTC-naive both sides), where `TRANSCRIPTION_LOOKBACK` is one constant
  in `transcription_jobs.py` that also feeds the poller's
  `--lookback-hours` default (24 h; PR 4 adds the mirrored
  `LastModifiedDate` and the 45-day creation floor to the rule). Not in
  the SQL sort twin; sorting by state stays on the five values.

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

Measured on PROD 2026-10-21, Hub-scope CloudCall calls created
2026-09-01 → 2026-10-16 with a URL: the stuck Confirmation / Follow-up
Tasks are indistinguishable from the transcribed Appointment / KDM
Pitched ones on every Task field we mirror — same owners (the same SDRs
and account managers, Fulfillment and Account Management), same CloudCall
accounts (`synety__i_account__c`), outbound, created 3–8 minutes after
the call, 36-char session ids, `api.us.cloudcall.com` URLs, no campaign
name, `Call_ID__c` set. Only the disposition and the duration differ
(avg 114 s / 142 s vs 166 s / 338 s). So the loss is not in our SOQL or
in who places the calls; it is on the CloudCall side of the match
(listing, leg, or recording not ready), and the client's own SF batch
misses them too (the URLs arrived in bulk from the refresher). The
stamper already logs its skip counters per tick
(`no_session_match` / `ambiguous_legs` / `recording_not_ready`), so
CloudWatch may answer this without code; per-disposition logging is the
fallback.

Run `stamp-cloudcall-urls --dry-run` style logging for one working day
and count, for confirmation / follow-up Tasks in the window, which
branch loses them: no listing record for the SessionID, no `Leg == 1`,
several `Leg == 1`, or `CallRecordingAvailable` false. The answer decides
whether `cloudcall-url-heal.md` Part B is the fix or whether these calls
are placed through a path CloudCall lists differently.

## Tests

- `claimed_sf_task_ids`: three `failed` rows → claimed regardless of age;
  two → not claimed once older than 6 h (the existing cooldown, unchanged).
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
   (`MAX_FAILED_ATTEMPTS = 3`), tests. Standalone (decided 2026-10-21):
   harmless before the sweep, required once it runs.
4. **udab-server: select on LastModifiedDate.** `JobCriteria.modified_since`
   + the SOQL clause; the tick passes `modified_since = now − 24 h` and
   `created_since = now − SCHEDULED_CREATED_FLOOR_HOURS` (45 d); both in
   `filters_json`. Hub flag reads the mirrored `LastModifiedDate` and the
   floor. Tests: SOQL builder, tick criteria, Hub boundary. No infra.
   Verify after the next refresher run with the stuck-count query in
   NOTES.md and the guard log line.
5. Stamper diagnosis (§3), separately. Still worth doing: it decides
   whether the URL could arrive in minutes instead of weeks.

Then tell the client: Confirmation / Follow-up calls transcribe within
minutes of their recording arriving; "Not transcribed" means older than the
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
- PR 3 (udab-server, branch `cap-failed-transcription-retries`,
  2026-10-21): `MAX_FAILED_ATTEMPTS = 3` next to `FAILED_RETRY_AFTER_HOURS`;
  `claimed_sf_task_ids` unions its per-row predicate with a per-Task
  `GROUP BY … HAVING COUNT(*) >= 3` over `failed` rows (one round trip
  per chunk, served by the existing `(sf_task_sf_id, status)` index).
  Three tests against the local MySQL: two old failures retry, one fresh
  failure claims, three failures claim regardless of age. Tests green.
  Merged as #834 (2026-10-21).
- PR 4 (udab-server, branch `select-on-last-modified`, 2026-10-21):
  `JobCriteria.modified_since` → `LastModifiedDate >= …` in
  `build_task_soql`; the tick passes `modified_since = now − 24 h` and
  `created_since = now − SCHEDULED_CREATED_FLOOR_HOURS` (45 d), both in
  `filters_json`; `--backfill` keeps its CreatedDate window. The Hub's
  `_base_select` gains `task_modified_at` and `transcription_window_open`
  requires both windows. Tests: SOQL builder (clause present / absent),
  tick criteria and filters, backfill unchanged, Hub item at four
  boundaries (fresh, untouched old, re-stamped within the floor,
  re-stamped beyond it). Tests green.

## Open questions

- Window length: 45 days covers what PROD showed; the client's refresher
  cadence is unknown. If it stamps older than that, raise the window
  (cost is SOQL pages, linear).
- Stamper: why confirmation / follow-up calls never match (§3).
