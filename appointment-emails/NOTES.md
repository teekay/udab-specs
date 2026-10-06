---
kind: notes
status: done
area: appointment-emails
updated: 2026-10-07
repos: [udab-server, udab-client]
summary: "Living reference: how the Appointment Calls queue works today (population, kinds, transcript state, export); specs are history."
---

# Appointment Calls queue — how it works today

## Empty cells: what a dash means — draft spec (2026-10-06)

Spec: [hub-empty-cells.md](hub-empty-cells.md). Client wants "—"
replaced by a reason. Five classes of empty (not on the call / not yet
/ never / blank in SF / a bug), counted on PROD; one word per cause.
**Built 2026-10-06 on branch `hub-empty-cells` in both repos,
uncommitted.** Rule: "N/A" only where the column cannot apply to the
row (briefing columns on non-booking kinds, talk share before
2026-09-15); everything else names what is missing. Adherence column
hidden on the Pitches view. New item field `review_window_open`.

- **Bug, fixed on the branch:** transcribed calls under `MIN_CALL_SECONDS` (20) never
  get an `sp_call_insight` row (`_select_work` filters them out), and
  the client reads "transcribed, no row" as Pending — 404 calls on
  PROD show "Pending" forever. Fix in the sweeper, not the API.
- **Sweeper leak, fixed on the branch:** `_select_work` selects every disposition in
  `CALL_DISPOSITION_MAP` (Gatekeeper, Contact, Left Live Message
  included); `kind_for()` rejects them as `unsupported_disposition`.
  610 wasted rows, ~300/month.
- Model nulls are findings: every prompt field says "Null if none was
  stated", so "Not stated" is the truthful cell text; on PROD Budget
  is null on 84 % of completed appointment insights, Competition 61 %.
- Scorecards: a Pipeline card exists at booking but gets stars after
  the meeting; this month 618 of 934 appointment calls have a card and
  0 have stars. "Not scored yet" vs "Not scored" must split on the
  60-day window (`SCORECARD_WINDOW_DAYS`); DARTS is scored on only
  19–25 % of cards even when old.
- **Talk Tracks are on hold (client, 2026-10-06): archive all,
  redesign later.** `sp_call_adherence` has no row since 2026-09-25
  and none will come; the Hub's adherence column has no source.
  Proposed: hide it until the redesign.

## Hub sorting on every column but Callback — shipped 2026-10-07

Spec: [hub-sorting.md](hub-sorting.md) (PROD timings per candidate,
scorecard index A/B). Branch `hub-sorting` in both repos.

- **Sorting needs no index on the sorted column here.** The key select
  ranges `sf_task` on `ix_sf_task_disposition_created`, joins or
  subqueries the sort value per population row and filesorts; the sort
  column always lives on another table, so only the lookup index into
  that table matters, and every candidate had one. Cost is the lookup's
  rows per task × the (period-bounded) population.
- **Callback is the one column that cannot sort cheaply**: no row stores
  "next call on this contact", it is a self-join on `sf_task` that
  walks ≈ 24 tasks per contact (PROD: 2.9 s for a month of pitches,
  26 s all time). Showing it costs ≈ 50 ms per 200-row page because the
  item subqueries run per page row only. Sort it only after
  materialising `next_call_at` on the task (sweeper).
- **Scorecard sorts** (grade, DARTS) walk the contact's scorecards per
  task; `ix_sf_quality_scorecard_contact_type_date` (migration
  `c7e2a9d4f153`) cuts the rows read from ≈ 24 to < 1 on PROD data
  (PROD after deploy: all time 3.8–4.0 s → 0.83–0.86 s, month 0.5 s →
  0.11 s).
- **Argo does not run migrations any more** (noticed 2026-10-07: deploy
  left `alembic_version` behind; DevOps ran it by hand). Check the
  reader's `alembic_version` after every deploy that carries one.
- **Key select carries the sort value** (`sort_N` labels) so the outer
  page orders by `page_keys.sort_N` and never re-evaluates a subquery or
  needs the sort's join. Ordering by a select-list alias is
  plan-neutral on MySQL 8 (timed).
- **Nulls last in both directions** for insight, scorecard, email and
  adherence sorts (`NULLS_LAST_SORTS`): most rows have no value.
  Picklists (agreement to meet, objection handling) rank by meaning via
  a `CASE`, not alphabetically; keep `AGREEMENT_ORDER` /
  `OBJECTION_HANDLING_ORDER` in step with the client badge sets.
- Client: score and date columns open descending (`firstSortDir`),
  names ascending.
- Not built, noted: column hiding (Excel-style, per user) as its own
  feature — the client's "can't get everything in one view" ask.

## Hub query shape after the performance pass — shipped (udab-server #792, udab-client #352; 2026-10-01)

Spec: [hub-performance.md](hub-performance.md) (PROD measurements, the
rewrites timed on the replica, the covering-index A/B). No migration.

- **Default period is "This month"** (client ask, 2026-10-02):
  `DEFAULT_PERIOD` in `src/constants/appointment-calls.js`;
  `defaultFilters(today)` resolves its dates, so Clear filters and a
  first visit are bounded. A stored view with no period and no dates
  (including the pre-preset shape) falls back to it; only an explicit
  `period: "all_time"` is unbounded. The API is unchanged: no date
  params still means all time. This is the biggest first-load win —
  PROD default-view SQL ≈ 0.84 s → ≈ 0.14 s (appointments), ≈ 2.3 s →
  ≈ 0.38 s (pitches).
- **Count runs alongside the page query.** The list route takes a
  second reader session (`Depends(get_async_readonly_db,
  use_cache=False)`) and `list_calls(..., count_db=)` gathers the two
  statements; with one session (tests, other callers) it stays
  sequential. Wait = max(count, page) instead of the sum; matters on
  wide ranges. Each list request holds two pooled reader connections
  for its duration.

- **Page = keys first, items second.** `list_calls` sorts and limits a
  slim id select (`_population_select`, only the joins the filters and
  the sort reference, see `ListFilters.joins()` / `sort_joins()`), then
  `_base_select(keys)` joins the item columns and the seven correlated
  subqueries onto that page of ids. Any sort other than `called_at`
  used to evaluate every subquery for the whole population before
  sorting (PROD: 8–57 s); now ≈ 0.5–2 s.
- **Count takes only the joins its criteria need.** MySQL never drops
  an unused outer join; `teams` needs the account owner, `search` the
  owner/contact/lead, everything else none. −40–45 % on PROD counts.
- **`/filters` is one pass + a 5-minute per-process cache**
  (`FILTER_OPTIONS_TTL_SECONDS`): distinct `(AccountId, OwnerId)` pairs
  over the task side of the population, then the account rule and name
  lookups on that small set; the five lists are sorted in Python with a
  casefold key (the collation is case-insensitive). Tests call
  `reset_filter_options_cache()` between cases. PROD: 19 s → ≈ 1.2 s
  uncached.
- **GET routes use `get_async_readonly_db`** (reader endpoint, pool
  20 + 30); the export `POST` stays on the writer. The test harness
  overrides both dependencies.
- `population_filters()` is now `task_population_filters() +
  account_population_filters()`, so a task-only pass can be built.
- Local data: `scripts/local/seed_appointment_calls_enrich.py` adds
  scorecards, adherence and insights to the volume seed so the per-row
  subqueries have rows to hit; `explain_appointment_calls.py` compiles
  the new statements (including the one-pass `/filters`).
- Not done, by decision: the `sf_task` covering index (≈ 1.5 GB on
  PROD for another ≈ 2× on counts and an uncached `/filters`).
- **What this pass does not change: first paint of the default view.**
  The default `called_at` sort was already "sort first, subqueries for
  one page"; the branch saves only the count's unused joins there
  (local, last month, 200/page: 503 ms → 455 ms server). First paint is
  dominated by things outside SQL — see "First paint, measured" in the
  spec.
- **Local gotcha — collation drift.** PROD's `sf_task.WhoId` is
  `latin1_swedish_ci`, matching the scorecard model's `Contact__c`; the
  local `sf_task` is `utf8mb4_general_ci`. With mismatched collations
  the scorecard subqueries cannot use `ix_sf_quality_scorecard_contact`
  and the local page costs 3–4 s instead of 0.5 s. The enrich seed
  now aligns the local scorecard column to `sf_task.WhoId`'s collation;
  PROD needs nothing. Bulk inserts also leave InnoDB stats stale for a
  while (the seed runs `ANALYZE TABLE`).
- Follow-up candidate: the `transcript_state` sort still runs its four
  job-task subqueries per population row inside the id select (PROD
  1.25 s, local 2 s); a LEFT JOIN to a "latest job task per Task"
  derived table would drop that to the default sort's cost.

## Filter bar aligned with Talk Track Adherence — shipped (udab-client c35a0d1; 2026-09-30)

Client asked that a filter over the same data look and work the same on
every page. The Hub's filter bar now uses the Adherence chrome; the
Adherence page changed only by extraction (no functional change).
Client-only; no server or data change. (Header corrected 2026-10-06:
this and the sections below were still labelled "unmerged" after they
shipped; the Pipeline-scorecard grade shipped as udab-server #787 /
udab-client #347.)

- **Shared pieces** (`udab-client/src/components/reports/`):
  `report-theme.css` (the `--report-*` palette, light + dark, on
  `.report-theme`), `ReportFilterPanel.vue` (card, heading, optional
  `actions` slot, `row g-3` body — controls go in `col-md-3`, a
  `.filter-break` div forces a new row), `ReportSelect.vue` (native
  select wrapper with the caret), `ReportDateRange.vue` (the popover,
  formerly `AdherenceDateRange`; `id-prefix` names its elements,
  `all-time-unbounded` makes "All time" clear both dates, emits `change`
  once on close when something moved). `useReportPeriod` (composable)
  holds the preset ↔ dates logic and the once-a-minute Central-day
  re-resolve; `onApply` fires when the tick moved the dates.
- **Hub filter state**: `dateRange` → `period` / `startDate` / `endDate`
  (default was `all_time`; **`this_month` since 2026-10-02**, see the
  performance pass above). Stored preferences are migrated on
  read (legacy pair → custom range; a stored preset is re-resolved
  against today, so "This month" follows the calendar).
- **Day bounds are Central now.** The list and export send
  `date_from` / `date_to` as UTC instants of Central midnight and
  23:59:59.999 (`centralDayStart` / `centralDayEnd` in
  `report-periods.js`); the server's `parse_date_bound` already takes
  the inclusive datetime path for these. Before, bare dates were read
  as UTC days while the column said CST.
- **Deliberately kept from the Hub, not Adherence**: commit on dropdown
  close, debounced search, Clear filters, per-user persistence,
  population-wide options (Adherence scopes options to the range).
- **Not aligned (decisions pending)**: Team stays the account owner's
  team over raw picklist values (Adherence: rep's team, active teams
  only, cascading rep roster); custom "To" is not clamped to today.
- Placement: the panel sits inside the Hub card under the view pills
  (title "Filters"); Adherence keeps its own card. Might move later.
- Order mirrors Adherence: call attributes first (Called, Kind,
  Recording, Search), then identities (Team, Rep, Account, Account
  owner, Industry), then the "Coming soon" placeholders.

## Call insights (AI extraction) — shipped (udab-server #778 + #783, udab-client #335; 2026-09-25)

Spec: [call-insights.md](call-insights.md) (its Implemented section is the
code map). Adherence-pattern feature: `sp_call_insight` (+ `_item`),
`call-insights-generate` sweeper, Sonnet via `app/services/bedrock.py`,
console item gains `insights`; client renders it in the table
(`AppointmentCallInsightCell.vue`), a flyout Insights tab, and a
client-safe switch that hides coaching and flags.

- **No local real-model path.** Local boto3 carries MinIO credentials, so
  the sweeper cannot reach Bedrock from the dev stack. A provider switch
  to the Anthropic API was built and dropped (dependency churn; see the
  spec). Prompt iteration happens in `udab-call-insights-poc/`; the
  server is tested against mocks. If the switch is ever revived: the SDK
  needs typing_extensions>=4.14, anyio>=4.10, idna>=3.18, h11>=0.16 with
  httpcore>=1.0.9, and Brotli>=1.2.0.
- **Kind comes from the disposition map**, not from the transcript row's
  `context_kind` (which means "appointment-email draft exists").
- **Deterministic corrections live in code, not the prompt**: swapped
  speaker labels are repaired before prompting (46+ calls in the booking
  corpus have them), asks after the agreement turn are dropped, stray
  quotation marks stripped. The model's own `speaker_labels_suspect` is
  kept as a secondary signal.
- **Local sample from production**: after `load_sample_calls.py`, run
  `scripts/local/sync_sample_from_prod.py` (host, VPN up, venv python) to
  copy the rows that surround the sample calls from the prod read replica:
  real contacts (company, email, mailing address; the sample tasks are
  re-pointed at them), later calls on those contacts, quality scorecards,
  adherence rows, Dani's summaries and highlights, and briefings with their
  images. Everything on the console then renders from production data
  except talk share (transcript JSON lives in S3, unreachable locally).
- **Local sample**: `scripts/local/load_sample_calls.py --tier core|wide|all`
  loads the PoC eval sets (real transcripts) as SF mirror rows; `--clean`
  removes them (marker `SMPL` in synthetic account/user/contact ids; task
  ids are the real SF ids). The volume seed's 14k fake transcripts are
  also eligible for the sweeper — always pass `--sf-task-id` locally.
- Gotcha: `.txt`-only transcripts (pre-JSON era and the local sample) give
  no `end` offsets, so `rep_talk_share_pct` is null for them.
- **Console columns that need no model** (2026-09-29): Company =
  contact `Company__c` (lead `Company`); DARTS = newest
  `sf_quality_scorecard` for the contact with `Appt_Date__c` within 60
  days after the call (scorecards never reference the Task; ~5 reviewer
  rows per appointment); Talk track adherence = completed
  `sp_call_adherence.adherence_pct`; Callback = first later `Call` task
  on the same contact (Salesforce follow-up tasks are essentially never
  logged: 1 open future task across 26k recent pitch contacts). All
  scalar subqueries on the list query.
- **Prompt r5** (supersedes r4 before merge): attendees (items), heat 0–5,
  objection handling, coaching note on bookings, timestamped moments
  (`start_seconds` on items, `agreement` item kind) — one migration
  `b8d4f0a1c2e3`; regrade after deploy. Scorecard components exposed as
  `scorecard` on the console item (no per-letter DARTS in Salesforce).
- **Building satellite fallback**: contact mailing address → Static Maps →
  S3 `building-images/{task}/{addr-hash}.png`; 7-day lifecycle rule on the
  prefix is the only invalidation (deploy checklist).
- Local dev API **does auto-reload**: the container runs
  `watchmedo auto-restart --pattern='*.py' -- uvicorn …`, so any `.py`
  change under `/app` (an edit, a branch switch) restarts the server
  process within a second or two. No `docker compose restart` needed.
  The container's own uptime does not change on a reload, so it says
  nothing about which code is loaded; check `docker logs` for
  "Started server process". (Corrected 2026-10-02: this note used to
  claim there was no reload. In-process state such as the `/filters`
  cache is lost on every reload.)

## Round 5 (Sales Enablement tab) — draft, questions with the client (2026-09-23)

Spec: [call-queue-sales-enablement.md](call-queue-sales-enablement.md).
The client asked for 16 Sales Enablement reps "under Reps"; prod shows
their calls are all on Prospect-status accounts or bare Leads, so the
`Active` rule drops every one. Proposed: a third tab over a second
population (`population=sales_enablement`, rep `Department`), not a
relaxed rule. Tab, department rule, Lead-only calls, same permission,
columns and export decided by Tomas 2026-09-23; open with the client
2026-09-24: whether an SE "Appointment" is an appointment booked for
an Abstrakt AE (decides if the Kind labels apply). Nothing built.

## Round 4 ("Download all") — shipped (udab-server d987f75, udab-client cb937b1; 2026-09-22)

Spec: [call-queue-download-all.md](call-queue-download-all.md). A
dedicated permission (`Download All Appointment Calls`) shows a button
that zips **every transcribed call matching the current filters** in
an AWS Batch job (`appointment-calls-export`), no table: the `POST`
pre-signs the 7-day download URL up front, the job writes a status
JSON next to the ZIP under `exports/appointment-calls/`, the page
polls and keeps the job in localStorage. The selected-rows export and
the job share one pipeline (`ExportArchive`, `iter_export_batches`,
`run_export`); `EXPORT_S3_CONCURRENCY` is 10 (boto3's pool size).
Merge checklist in the spec's "Follow-ups at PR time".
## Round 3b — mockup-shaped presentation — shipped (udab-client #320; 2026-09-16)

Client-demo requirement: the page must *read* as the mockups taking
shape. Client-side only, on top of round 3:

- **Appointments | Pitches view switch** (nav pills) on the one page,
  mirroring the mockups' two consoles. Each view scopes the kinds
  bucket (the request now always sends `kinds`), restricts the Kind
  filter's options, and persists in view preferences (`view`).
- **Columns reordered/relabeled to the mockups** per view; built
  columns show real data, unbuilt ones a dashed **"Coming soon"**
  chip whose tooltip says what's pending (scorecard / AI phase /
  SF source). Mockup filters not built yet render as disabled
  "Coming soon" controls (grade; callback status / meat on the
  bone / asks).
- Our own columns (Kind, Transcript, Summary, Briefing) trail after
  the mockup set; selection/export unchanged. Header is no longer
  sticky — the mockup-wide table always horizontal-scrolls, and
  sticky can't survive an overflow wrapper.
- "Rep and owner" is one column (rep main; account owner · team
  sub), sorted by rep; account-owner sort still available via API.
  "Meeting date" sorts by call date until meeting-date sort lands
  (tooltip says so).

## Round 3 (consoles) — shipped (udab-server 07983d7, udab-client #320; 2026-09-15)

The Bucket-1 slice of consoles-mockup-analysis.md:

- **Account owner**: column (sortable, `account_owner_name`), filter
  (`account_owner_ids` over `SfAccount.OwnerId`), `/filters` gains
  `account_owners`. The join and item field already existed.
- **Meeting column + flyout Meeting tab**: the item's
  `appointment_email` payload gains `occurred_at`, `meeting_type`
  (contact snapshot `phone_in_person`), `address` (`resolve_address`
  over the snapshots) and `has_building_image`. Rendered as
  wall-clock — `appt_scheduled_at` is a local snapshot; the client
  formats the string (`formatMeetingTime`) instead of `new Date()`,
  which would shift it into the viewer's zone.
- **Building image**: `GET /appointment-calls/{id}/building-image`
  streams the stored `sp_appointment_email_image` bytes (property
  kind first, map fallback; population-gated, same permission). The
  client fetches it as an authed blob → object URL. No Google call,
  no key exposure.
- **Utterance end times**: `build_transcript_result()` now keeps
  `end` on every utterance in the stored result JSON — feeds future
  talk-share metrics; not backfillable, so shipped ahead of need.
- **Seed**: `seed_appointment_calls_volume.py` now writes
  `contact_snapshot` (meeting type + address) on seeded emails and a
  placeholder SVG building image for ~70% of them.
- Deliberately NOT built yet: meeting-date sort/filter (needs the
  email join in the access path — perf question), client-safe
  toggle (nothing to hide yet), highlight timestamps (prompt/QA).
- **Meeting type source — no meeting entity exists (verified on the
  PROD replica 2026-09-15).** The SF org models a booked appointment
  as mutable fields ON the person (`Appt_Scheduled_*` +
  `Phone_In_Person__c` on Contact; `Appt_*` also on Lead), one
  current appointment per person, overwritten on reschedule — hence
  the email pipeline's ContactHistory trigger + snapshot. SF Events
  (`sf_event`, 250k rows mirrored) are pitch-activity records
  (`Type='Pitch'`), not booked meetings, and carry no meeting-type
  field. `sf_contact` does not sync `Phone_In_Person__c`; the
  briefing `contact_snapshot` is the only local copy. PROD values
  over 5,391 emails: In Person 2,160 / Phone 1,693 / **Virtual 476**
  (not in the mockups) / null 1,062. Per-client default lives on
  `sf_qualified_appointment_sheet.Appointment_Process_Phone_or_In_Person__c`
  (3,096 of 8,565 filled).
  **Consequence:** `sp_appointment_email` is the de-facto meeting
  entity (one immutable row per booking flip). A meeting-type filter
  should promote `meeting_type` to a real indexed column on that
  table — stamped at creation, backfilled locally from existing
  snapshots (thousands of rows, one UPDATE, no SF re-sync). Do NOT
  add it to the `sf_contact` mirror: wrong grain (mutable per-person
  current value) and backfill would need an 11.4M-row contact
  re-sync. Meeting-date sort/filter rides the same join work
  (`appt_scheduled_at` is already a real column).
- Meeting data only exists for calls matched to a briefing; pitch
  rows show a dash by design.

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
  Pipeline Client / Active account, `CreatedDate >= 2026-01-01 UTC`
  (`APPOINTMENT_CALLS_SINCE`; was the 2026-08-25 13:35 poller go-live
  until 2026-09-23, when per-account backfills of older calls proved
  possible — CloudCall URLs re-mint by call id, Orum audio from March
  still fetched — and the client asked to see them here. Prod
  population at the change: ~26k rows since go-live, ~176k since
  2026-01-01, ~406k with no floor; 2025 and earlier have zero
  transcripts, so the floor stays at 2026). Served by
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

- Long-running read jobs (the "download all" export, anything chunked
  over the reader session): `commit()` between chunks. SQLAlchemy
  autobegins a REPEATABLE READ transaction on the first query and
  keeps it until the session closes; on Aurora a read view held open
  on a reader pins undo purge on the writer. Read-only, so commit is
  free.
- Route order matters: `/export` and `/filters` are registered before
  `/{sf_task_sf_id}`.
- `transcript_text()` is `load_transcripts()` for one id — don't loop
  it for batches (it runs the full joined query each time).
- Tests must run through `scripts/test.sh` (ephemeral DB); the fixture
  prefixes (`00TACQ`, …) keep purges away from other tests' rows.
- The item shape's `kind` is a free `str` in the schema; the client
  falls back to rendering the raw value for unknown kinds.
