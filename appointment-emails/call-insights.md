---
kind: spec
status: in-progress
area: appointment-emails
updated: 2026-09-30
repos: [udab-server, udab-client]
summary: "Per-call structured insights from transcripts: PoC done, V1 built locally on the adherence pattern; awaiting review."
---

# Call insights — structured per-call extraction for the Appointments / Pitches views

Status: IN-PROGRESS 2026-09-25 — V1 built locally in both repos on branch `llm-call-transcript-extraction` (uncommitted; Tomas reviews Sunday 2026-09-27). PoC rounds 1–3 done; see PoC log. Original framing follows. Client
ask arrived 2026-09-24 as "Appointment/Pitch Queue: Account Mgmt Hub"
(a per-column definition list for the two views). It narrows Bucket 3
of [consoles-mockup-analysis.md](consoles-mockup-analysis.md) to a
concrete field set. The surface is the shipped Appointment Calls page
with its Appointments | Pitches switch (round 3b, [NOTES.md](NOTES.md));
the "Coming soon" AI-phase chips there are the slots this spec fills.

Client framing (verbatim): "These need to be pulled out from the
transcription and stored similarly to how we did the summary and key
highlights. That way we are able to reference this for several
features and never pull in something different." A new table related
to `sp_call_transcript` is explicitly fine.

## Decision: build on the existing generator

The ask is the same shape as summary + highlights: one transcript in,
one structured payload out, stamped with model id, produced once and
read by every consumer. Everything needed exists in udab-server:

- `app/services/bedrock.py` — Bedrock Converse, one forced tool,
  strict Pydantic re-validation, token/latency usage. Sonnet 5 and
  Opus 5 constants.
- `app/prompts/*` — one module per artifact (`SYSTEM`, `TOOL_NAME`,
  `INPUT_SCHEMA`, payload model, `build_user_message`).
- `app/commands/call_transcript_generate.py` — the sweeper: claim,
  per-artifact guards, attempts/stale/defer, upgrade pass, on-demand
  path. Insights become the third artifact next to highlights and
  summary.
- `app/services/call_adherence.py` — the precedent for *graded*
  output: transcript rendered as numbered turns, Literal enums, turn
  ranges + evidence per claim, post-validation of turn refs
  (`_range`, speaker checks), usage columns on the result row.

No new provider, SDK, or job type. The AI assistant's agentic loop is
not needed here.


## Implemented (V1, 2026-09-25, local only)

Built on the adherence pattern rather than as a third artifact of Dani's
generate sweeper (decided with Tomas: independent scheduling, no 2-hour
deferral, his code untouched apart from the provider switch).

**udab-server** (`llm-call-transcript-extraction`)
- `app/services/bedrock.py` untouched: Sonnet via Bedrock through Dani's
  `converse_structured_with_usage`. A `LLM_PROVIDER=anthropic` switch was
  built for local real-model runs and then dropped (2026-09-28) to keep
  the PR's blast radius contained and avoid local/prod divergence: the
  SDK's HTTP client forced bumps to typing_extensions, anyio, idna, h11,
  httpcore and Brotli plus five new pins. Revisit if a second provider
  is ever wanted. Consequence: the sweeper cannot reach a real model
  from the local stack (boto3 carries MinIO credentials there); the
  PoC harness covers prompt work, and the server side is tested against
  mocks. No dependency changes in this PR.
- `app/prompts/call_insights_{common,appointment,pitch}.py`: PoC round-3
  prompts, `PROMPT_VERSION = "r3"`, nested models `extra="ignore"`.
- `app/models/call_insight.py` + migration `a7c3e1f2d9b4`: `sp_call_insight`
  (one per transcript; kind, status/claim/attempts like adherence, scalar
  and enum columns, counts, `rep_talk_share_pct`, `labels_swapped`,
  `validation_flags` JSON, model/prompt/usage) and `sp_call_insight_item`
  (opportunity | close_attempt | objection | flag; position, turn, label,
  text, quote, response). Raw SQL.
- `app/services/call_insight.py`: loads the result JSON (falls back to the
  `.txt` for older calls, so talk share is null there), preprocesses turns
  like adherence, repairs swapped speaker labels (vote-based; names from
  the SF contact/rep), builds context from SF account/contact/lead/user,
  one Sonnet call, strips stray quotes, drops close attempts after the
  agreement turn (`prospect_initiated` = no ask before agreement),
  validates (turn range/speaker/quote/ellipsis/neighbour; findings stored
  as `validation_flags` strings `error:|warn:|note:`), persists row + items.
  Not applicable: `unsupported_disposition`, `no_transcript`, `too_short`
  (< 4 turns). Selection minimum 20 s.
- `app/commands/call_insights_generate.py` (`call-insights-generate`):
  select new transcribed appointment/pitch calls + retries, claim by
  insert/conditional update, concurrency 3, 50 per run, 5 attempts;
  `--dry-run`, `--limit`, `--since`, `--transcript-id`, `--sf-task-id`,
  `--regrade` (reclaims completed rows whose prompt_version is behind).
- Console API: `AppointmentCallItem.insights` (nullable block, items as
  lists) via one more outer join in `_base_select` and a per-page loader.
- Tests: `tests/test_call_insight_service.py`, `tests/test_call_insights_generate.py`
  (15, via `scripts/test.sh`); `tests/test_appointment_calls.py` still green.
- Local: `scripts/local/load_sample_calls.py` loads the eval sample
  (ZIPs + CSVs under `scripts/local/sample-data/`, gitignored) as SF mirror
  rows + transcript rows + MinIO objects (used for UI work; the sweeper
  itself needs Bedrock). While the provider switch existed, the 24 core
  calls ran end to end through the server: 24/24 completed, 79 s,
  ~6.3k in / 950 out tokens per call; those rows are still in the local
  DB.

**udab-client** (`llm-call-transcript-extraction`)
- `AppointmentCallInsightCell.vue`: one cell per insight column with
  placeholders by state (Pending / Unavailable / —), urgency bars, pills
  with supporting line, stacked lists, counts with "Prospect offered".
- Page: AI "Coming soon" slots replaced per view (appointments also gain
  close attempts, objections, budget, talk share; pitches gain case study
  and flags); pitch Duration cell shows talk share; **client-safe view**
  switch hides coaching and flags in table and flyout (session-only).
- Flyout: **Insights** tab with grades, facts, opportunity list, close
  attempts and objections with turn refs and quotes, flags (with DNC /
  wrong-contact / labels-corrected badges), coaching, and a collapsed
  generation-notes block; hidden parts follow the client-safe switch.
- Tests: cell component, constants, page and flyout cases added; whole
  suite green.

**Console columns filled without the model (2026-09-29, local, unmerged)**
Client answer: Company = the contact's company. Production facts checked
on the reader endpoint the same day:
- `sf_quality_scorecard`: 454k rows since 2011, linked by `Contact__c` +
  `Appt_Date__c` only (never by Task); ~5 rows per (contact, date) since
  Aug 25 (reviewer versions); 5,011 of 5,831 bookings since Aug 25 have
  one within 60 days. `DARTS_Score__c` is a 0–100 percentage, filled on
  every row (12 distinct values). `Opportunity_Score__c` is filled on 766
  rows only, values 0/1/2 — **not** the 5-star grade; that column stays
  "Coming soon".
- Adherence: 10,179 completed `sp_call_adherence` rows + 8,291 Engage rows.
- Follow-up tasks in Salesforce: 1 open future task across 26,005 recent
  pitch contacts — the mockup's "reads the next SF activity" callback has
  no data behind it. Replaced by **the next call logged on the same
  contact** (date + kind/disposition, or "No callback yet").
- Contact `Company__c` filled on 99% of booking contacts.

Implemented as scalar subqueries on the list query (no migration):
`contact.company` (contact `Company__c` / lead `Company`), `darts_score`
(newest scorecard for the contact with `Appt_Date__c` within 60 days after
the call), `adherence_pct` (completed adherence row on the transcript),
`next_call` (first later `Call` task on the same contact). Client renders
Company (both views), DARTS (appointments), Talk track adherence and
Callback (pitches); flyout header shows the company. Still "Coming soon":
Opportunity grade only — deferred, see the Opportunity grade note above
(5-star record type live 2026-10-01, fields not yet synced). HEART is
retired in its favour.

**Round 4 (2026-09-29, local, unmerged) — prompt r4 + migration `b8d4f0a1c2e3`**
- Appointment payload gains `meeting_attendees` (prospect-side people who
  will attend: name, role, relation contact|other; never the rep's side),
  stored as `attendee` items (text=name, label=role, response=relation).
  Client confirmed group meetings are common, so this is a list on
  purpose; not filterable.
- Pitch payload gains `opportunity_heat` 0–5 ("meat on the bone": 5
  seeking a vendor now … 0 not relevant; ties resolve downward) with an
  evidence quote, and `objection_handling` strong|adequate|weak|none.
  Each objection carries a `technique` (feel_felt_found | acknowledged |
  reframed | ignored) in the item's `label`. Objections are now "each
  distinct reason once; a repeated refusal is not a new objection".
- Code rules: do-not-call caps heat at 1; a meeting agreed on the call
  floors it at 4; handling is forced to `none` without objections and
  derived (weak if every technique is ignored, else adequate) when the
  model says `none` despite objections. Prompt asks for exactly the
  tool's fields and no others.
- Migration adds three nullable columns to `sp_call_insight`
  (`opportunity_heat`, `opportunity_heat_evidence`, `objection_handling`);
  parent `53b419c5b78a` (Dani's Engage recorded-line migration, which
  landed after our merge).
- Client: Meeting with (appointments), Meat on the bone and Objection
  handling (pitches) replace their chips; flyout shows attendees, heat
  with evidence, handling, and the technique badge per objection. Also
  today: Company (contact's company, per client), DARTS (scorecard
  join), Talk track adherence and Callback (next call on the contact).
- Checked on the real model through the PoC harness (24 core calls):
  heat 1–4 across the pitch sample and matching the selector's notes,
  the DNC cap fired on all three DNC calls, techniques plausible,
  attendees named with roles after the prospect-side restriction. One
  model miscite (a close attempt on a Prospect turn) caught by the
  validator. Anchors for heat still need the client's confirmation.
- After deploy: run `call-insights-generate --regrade` (repeatedly, 50
  per run, or with `--limit`) so existing rows pick up r4; only pitch
  rows change materially, appointment rows gain attendees.

**Building satellite view for calls without a briefing (2026-09-29, local, unmerged)**
The mockup shows a satellite image only (the earlier "street view" mention
in consoles-mockup-analysis.md overstated it). Briefing-backed calls already
had a stored image; 3,687 of 5,702 recent bookings. For the rest: the item
gains `building {address, source: briefing|contact, has_stored_image}`
(address = briefing snapshot, else the contact's mailing address; 5,549 of
5,702 recent booking contacts have one; leads carry only a street, so no
lead fallback). `GET /{id}/building-image` falls back to
`fetched_building_image`: one Static Maps satellite call (640x400, zoom 19)
with the settings key, bytes kept in S3 at
`building-images/{sf_task_sf_id}/{sha1(address)[:12]}.png` and served from
there afterwards. Cache policy is deliberately dumb: a corrected address is
a new key; everything else ages out under a **7-day S3 lifecycle rule on
the `building-images/` prefix** (same pattern as the export ZIPs), and a
missing object just means "fetch again". Verified locally with the
production key on the ATCO sample call: 640x400 image served and cached.
No migration. Client: the Building cell shows a 120×72 satellite thumbnail
(`AppointmentCallBuildingThumb.vue`, fetched through the authed route only
when the row scrolls into view, so a 200-row page does not fire 200
requests) with the address and a Maps link under it, matching the mockup's
in-row map; the flyout's Meeting tab shows the full-size Building card
whether or not a briefing exists.

**Round 5 (2026-09-29, local, unmerged) — the mockup drawer, prompt r5**
Everything the mockup stacks under Building in the drawer, except HEART
(part of the client's pending opportunity grade):
- **Key moments with timestamps**: `sp_call_insight_item.start_seconds`
  (added to the still-unmerged migration `b8d4f0a1c2e3`) carries the cited
  turn's offset for close attempts, objections, and a new `agreement` item
  (the acceptance as a moment). The flyout's Summary tab lists them in
  recording order as mm:ss. Dani's highlights stay untouched.
- **Coaching note on bookings**: appointment payload gains `coaching_note`
  (same column as pitches); flyout shows it for both kinds, hidden by the
  client-safe switch.
- **DARTS gate**: `sf_quality_scorecard` holds several record types; the
  DARTS card is the `Dart_Scorecard` type (5,342 since Aug 25). Its
  `DARTS_Score__c` is points over eleven, fitted exactly across all of
  them: D Desire to meet (`Desire_to_Meet__c`: No Objection 2 / Two or
  Less 1 / Three Plus 0), A Authority (`Authority__c`: Final KDM 2 /
  Influencer 1 / Neither 0), R Revenue opportunity
  (`Revenue_Opportunity__c`: Immediate Need 3 / Interest 2 / Willing to
  Meet 1 / No Interest 0), T Timeliness (`Timeliness__c`: 0-6 months 2 /
  6-12 1 / 12+ 0), S Size (`Size__c`: Huge Opportunity 2 / Qualified 1 /
  Not Qualified 0), each with a reviewer note in `D_Notes__c` …
  `S_Notes__c` (filled on ~5% of rows; the notes read like a generated
  audit with transcript citations). The item exposes `scorecard {points,
  max_points, letters[{letter, name, level, points, max, note}],
  recap_notes, appt_date}`; the flyout draws each letter as pass (full
  points), partial, fail (zero) or unscored, with the note beneath.
  Join: newest `Dart_Scorecard` for the contact with `Appt_Date__c` within
  60 days after the call.
- **Opportunity grade — interim Pipeline stars (2026-09-30).** The client
  asked for the Pipeline scorecard result in the column until the 5-star
  card is generated, with a label naming the card type. Prod agrees: in
  September 2026 Pipeline cards were the only grade produced at volume
  (8,348 created, 3,316 scored, versus 822 scored DARTS and 4 empty
  5-star pilots). The item now carries `opportunity_grade {source:
  "pipeline", stars, max_stars: 5, appt_date}` from the newest Pipeline
  card with a star count (same contact + 60-day join as DARTS; zero-star
  shells skipped). Column keeps the "Opportunity grade" label and draws
  stars with a "Pipeline" tag; the flyout Insights section shows the
  same card, visible in the client-safe view. Stars in prod run 1–4 in
  practice. When 5-star rows appear, add `source: "five_star"` preferring
  the Task-referenced card — no UI change needed beyond the label map.
  The 5-star sync work below still stands:
  - The grade is a new record type on the same Salesforce object the
    DARTS and Pipeline cards use (`Quality_Scorecard__c` → `sf_quality_scorecard`):
    developer name `X5_Star_Scorecard`, record type id
    `012Rj000001kYSjIAM`, "rolling out officially 10/1"; it replaces
    HEART. Four hand-made pilot rows exist in prod (16–28 Sep), status
    Open, every score field zero, referenced by nothing.
  - **Rows of the new type sync already** (the sync selects all fields
    and stores the record type name). **Its new fields do not**: the
    scorecard processor maps 145 fields by name, generated 2026-09-18,
    and the record type's picklists ("A - Actively Listen", "About Us",
    "3 Step Intro", "1st Open-Ended Question", "4 Goals of Pipeline",
    "You know as well as I do", …) have no column. Of the eight picklists
    seen, one is synced (`Accuracy_of_Information__c`), one maybe
    (`Caller_used_Worst_Case_Scenario__c`), six are not. The exact delta
    needs an object describe; the local SF credentials fail
    (`invalid_grant`), so Dani (owner of the sync) or the prod credentials
    are needed for that. Then: migration + model + processor mapping
    lines + backfill of the pilot rows (Dani's side).
  - **Which field carries the star total is unknown.** `Stars_Count__c`
    (fraction of five) and `X5_Star_Score__c` (4 per star) exist and are
    synced, populated today only on `Pipeline_Scorecard` rows (3,970 of
    5,887 recent bookings have one within 60 days). If the new type
    writes there, the grade needs no sync change.
  - **Exact join exists via the Task**: Salesforce auto-creates two
    scorecards per booking and stamps them on `sf_task` —
    `Client_Scorecard_ID__c` → `Client_Appointment_Scorecard`,
    `Abstrakt_Scorecard_ID__c` → `Non_Appointment_Scorecard` — filled on
    5,576 of 5,887 recent bookings, all resolving to synced rows. The
    client card is an empty shell today (211 of 5,895 have a score).
    Expected rollout: the auto-created row becomes `X5_Star_Scorecard`
    and `Client_Scorecard_ID__c` points at it, giving the grade an exact
    join with no date window. `Contact_Score__c` on the Task is unrelated
    (values 8–33 on bookings whose card is empty). DARTS rows are never
    Task-referenced; their contact + date join stays.
  - **To resume**: (1) first completed 5-star row in prod → confirm the
    star field and the Task reference; (2) Dani's sync migration for the
    new picklists if the console should show them; (3) resolve
    `Client_Scorecard_ID__c` / `Abstrakt_Scorecard_ID__c` on the item and
    render stars when the referenced row is `X5_Star_Scorecard`. HEART
    is gone with it.
- **Record footer** (Task id link, contact email, briefing id) on the
  Summary tab; `contact.email` added to the item.
- Building card links to Google Maps for contact-sourced addresses too.
- Local rows regraded to r5 through the real sweeper (Anthropic transport
  swapped in for the run only): 24/24 in 88 s.

**Deploy checklist (not done)**
- Run migration `b8d4f0a1c2e3` (heat, objection handling, item timestamps; the first one, `a7c3e1f2d9b4`, is deployed).
- Add the S3 lifecycle rule: expire objects under `building-images/` after 7 days.
- Schedule `call-insights-generate` as a Batch job like adherence; first
  runs backfill the population since 2026-08-25 at 50 per run (raise
  `--limit` or run repeatedly); Sonnet on Bedrock ≈ $0.025 per booking.
- Utterance `end` times exist only for calls transcribed after round 3
  (2026-09-15); talk share is null before that.

## Field inventory

Legend — **LLM**: extracted by the model. **Derived**: computed in
code, no model. **Held**: not in the first schema.

### Shared (both views)

| Field | Source | Shape | Notes |
|---|---|---|---|
| Opportunity size | LLM | list of `{label, value}` items | Open-ended; the client's qualifiers sheet seeds *examples* only ("# of Buildings / Square Footage / AC areas"). Rendered stacked. Sheet not yet pulled in — needed before prompt writing. |
| Competition (was "Current Vendor") | LLM | text, nullable | Vendor name drop or coverage details. |
| Close attempts | LLM list → Derived count | list of `{turn, quote}` | Count = list length. Never ask the model for a bare number. |
| Objections | LLM list → Derived count | list of `{turn, objection, rep_response?}` | Same rule. Adherence prompt already has the "never infer an objection from a rebuttal" guard — reuse the wording. |
| Rep talk share | Derived | percent, nullable | Sum of `end - start` per speaker over the stored result JSON. Null when utterances lack `end` (calls transcribed before round 3). Not an LLM field. |
| Flags | LLM (transcript-only) | list of `{code?, text}`, empty = "No flags" | e.g. "Work just completed", "Reluctant agreement", "Decision process not asked". Record-mismatch flags (address on contact vs. stated) are **held** — they are SF comparisons, not transcript reading. |

### Appointments view

| Field | Source | Shape | Notes |
|---|---|---|---|
| Reason for meeting | LLM | text | "Drop-by introduction so they are a known resource when roof work goes out to bid". |
| Pain or need | LLM | text | |
| Urgency | LLM | enum, 5 levels | Client wants five; their examples are three ("No active project" low, "Quote now, unsure which budget year" medium, "Budget review now, final decision EOY" high). Anchor descriptions for all five needed from the client, or we propose them. Model also returns the supporting quote. |
| Timeline | LLM | text | Budget/project timeline, buying window. |
| Decision maker | LLM | enum `decision_maker` / `influencer` / `unconfirmed` + `detail` text | Pill with details underneath. |
| Budget | LLM | text, nullable | |
| Agreement to meet | LLM | enum `strong` / `open` / `reluctant` + verbatim `quote` | Client asked for a middle value; `open` is the proposal. Quote must be verbatim — verifiable by substring match. |

### Pitches view

| Field | Source | Shape | Notes |
|---|---|---|---|
| Reason to call back | LLM | text | What could be leveraged on a callback to set the appointment. |
| Opportunity details | LLM | same as Opportunity size | Same framework as appointments. |
| Case study / differentiator | LLM | `provided: bool` + `text` | "Not provided" when false. |
| Coaching note | LLM | text | What would have landed the appointment. Rep-facing; hidden by the client-safe toggle. |
| Meat on the bone | **Held** | enum 1–5 | Still undefined per the 2026-09-15 stakeholder input ("No opportunity now, but review materials" = middle). Needs anchors before it is a prompt. |

## Architecture

### Which kind drives the prompt

Two prompt modules (appointment, pitch) sharing sub-models. The
generator's existing `context_kind` ("appointment" when a live
appointment-email draft exists, else "call") is a *framing* choice
for summary/highlights, not the call's disposition. The client's views
are by disposition, so the insight kind must follow
`CALL_DISPOSITION_MAP` (appointment vs pitch), same as the queue's
`kind`. A transcript whose disposition is neither gets no insights.
Decide whether to also store the resolved `kind` on the insight row
(yes — it is what the row was generated for).

### Transcript input

Not the head-and-tail `truncate_transcript` (60k chars) — cutting the
middle breaks counted lists. Use the adherence path: load the result
JSON, `preprocess_turns` → numbered `[#n] [MM:SS] Speaker:` turns, so
the model cites turn indexes and quotes can be checked. Size policy is
open: appointment calls are typically short, but we need the length
distribution before setting a cap (PoC output).

### Payload and validation

One forced-tool call per transcript. Strict Pydantic (`extra="forbid"`,
`strict=True`), Literal enums for every pill, turn refs `int | None`.
Post-validation in code as adherence does: turn in range, cited turn's
speaker is the prospect for objections and agreement quotes, quote is
a substring of the cited turn (normalized). A failed check downgrades
the field to null with a stored validation flag rather than failing
the row — decide per field in the architecture round.

### Storage

`sp_call_insight` (name open): one row per `sp_call_transcript`
(unique FK), `kind`, scalar/enum columns for every single-valued field,
`model_id`, `prompt_version`, `input_tokens`, `output_tokens`,
`latency_ms`, `generated_at`, `validation_flags` JSON. Multi-valued
items (opportunity size, close attempts, objections, flags) in a child
table `sp_call_insight_item` (`kind` = opportunity | close_attempt |
objection | flag, `position`, `turn`, `label`, `text`, `quote`) so
they stay queryable and exportable. JSON-column alternative is
simpler but blocks the free-text search the mockup analysis flagged.
Talk share lives on the same row (`rep_talk_share_pct`, nullable) but
is computed, not generated.

### Generation

Third artifact in `generate_for_transcript`: its own guard, its own
stamps, its own failure isolation. Skip when `kind` is not
appointment/pitch, or the call is under the adherence-style minimum
length (own constant). Regrade: `prompt_version` on the row; the
sweeper's upgrade pass reselects rows whose version is behind. Model:
start on Sonnet (the tier highlights moved to 2026-09-21); the PoC
decides whether any field needs Opus.

### Surface

Item shape of `/appointment-calls` gains an `insights` block (nullable
until generated); the round 3b columns replace their "Coming soon"
chips per view. Coaching note and rep-facing flags fold under the
client-safe toggle. No editing/feedback loop in v1 — the highlights
feedback pattern exists if the client asks.

### Client feedback, round 1 (2026-09-29)

Built the same day, udab-client only:

- "Client" column moved to the first position in both views and renamed
  **Account**; "Rep and owner" sits right after it.
- The flyout is one scrolling page: the former tabs are now section
  links (Summary, Meeting, Highlights, Insights, Transcript, Recording)
  that scroll to their section and highlight the one in view. Transcript
  and building image load when the flyout opens instead of on tab select.

TODO (not built):

- Filters must be consistent with other pages wherever they cover the
  same information — the client named the Talk Track Adherence page.
  Reuse its filter controls/semantics (date window, rep, team, account)
  on the Appointment Calls page rather than inventing new ones.
- Opportunity grade filter (minimum stars / not graded). The disabled
  "Coming soon" placeholder was removed on 2026-09-30 when the column
  went live; wiring it needs a `min_stars` list param backed by the same
  Pipeline-card subquery, plus the persisted view state.

## PoC — before any schema lands

Goal: see real output on real calls, judge Sonnet, and find the fields
that need tightening or dropping. Manual QA by Tomas against the
transcript. Nothing written to the DB.

- **Harness** (built 2026-09-24): `udab-call-insights-poc/` in the
  workspace (uv project, own README). `prompts/` holds the two draft
  prompt modules in the `app/prompts` shape; `poc-run` loads exported
  `.txt`/ZIP transcripts, renders numbered turns, runs one forced-tool
  call per transcript (`--transport anthropic|bedrock|fake`,
  `--model sonnet|opus`), post-validates, and writes per call
  `review.md` (claim + cited turn text), `payload.json`, `prompt.md`,
  plus `summary.md` and a `qa_sheet.csv` with empty verdict/note
  columns. `--sample N --seed S` picks calls spread across the length
  range, so Sonnet and Opus runs use the same set.
- **Sample**: 10–15 calls, both kinds, mixed length (short, median,
  long), a few known-messy ones (single-channel warnings, automated
  voice). Pitch material already on disk: the Software Advice 2026
  export ZIP (~12.8k `KDM Pitched` transcripts, a different client but
  the same pipeline output). Appointment calls need a fresh bulk export
  from the Appointment Calls page (Kind = booking/confirmation).
- **Automatic checks in the harness** (these become the runtime
  post-validators): turn refs in range; quote is a substring of the
  cited turn; objection/agreement citations point at prospect turns;
  enum values present. Report violations per run.
- **QA sheet**: one row per call × field: claim, verdict
  (correct / partly / wrong / missing / not-applicable), note. Sum per
  field → which fields are reliable, which need prompt work, which
  are not extractable. Timeline, budget, and urgency are the likely
  hallucination magnets; close attempts and objections the likely
  miscount magnets.
- **Success bar**: no wrong verbatim quotes, no invented
  budget/timeline facts, enums defensible on re-read. A field that
  misses this on Sonnet gets a second run on Opus before it is
  dropped or reshaped.
- **Blocker**: Bedrock and prod S3 need an AWS identity. The app never
  holds AWS credentials — every boto3 client is built bare and relies
  on the default chain (the ECS/Batch task role in prod; MinIO dummy
  keys via env in local compose, which is why local Bedrock calls
  fail). `sp_setting` holds Batch queue/job-definition names only, and
  there is no Anthropic API key anywhere: Bedrock authenticates with
  the AWS identity. So the PoC needs either an IAM user / SSO profile
  on this PC with `bedrock:InvokeModel` on the two Claude inference
  profiles plus `s3:GetObject` on the transcripts bucket (ask Dani how
  he iterates), or to run as a one-off Batch job through `launch_job`
  writing its output to S3.

### PoC log

- **2026-09-24, round 1 (random 12 pitches, Sonnet):** zero validator
  errors, quotes verbatim, objections grounded. Defects were definitional:
  email asks counted as close attempts in 3/12 calls, sentence splits by
  interjections double-counting asks, boilerplate flags ("decision process
  not asked" on 8/12), one over-synthesised competition claim. ~3.6k input
  / 665 output tokens, 5–11 s, ≈$0.014 per call at Anthropic list price.
- **2026-09-24, fixed eval sets built** (two Opus selection agents, every
  pick read in full): `sample/pitch_sample.csv` (12 core + 20 wide from
  the Software Advice ZIP) and `sample/appointment_sample.csv` (12 core +
  20 wide from the 2026-09-24 booking export, 5,156 transcripts). Each row
  carries bucket ids and a note; the appointment rows carry the selector's
  own urgency/agreement read as a labeling aid. Selection scripts:
  `poc/select_sample.py`, `poc/select_appointment_sample.py`.
- **Corpus facts that shaped round 2** (now in the prompts): Prospect
  turns carry phone menus, hold ads and receptionists (~830 appointment
  and ~2,000 pitch calls); speaker labels are swapped end to end in some
  calls; ~10% of "bookings" have no acceptance or are confirmations /
  reschedules; some "KDM Pitched" calls end with an agreed time; urgency
  is mostly rep-led ("would you move in the next 12 months?" → "yeah"),
  so only volunteered statements count; the standard script sounds like
  a differentiator, so case-study needs an explicit exclusion; vendor and
  month names get garbled by transcription; the account name is a poor
  guide to the vertical.
- **Round 2 schema additions:** `agreement_to_meet` gains `none` (turn
  and quote nullable), `meeting_format`, and on both kinds
  `do_not_call_requested`, `wrong_contact`, `speaker_labels_suspect`;
  pitch gains `appointment_agreed`. Validators tolerate quotes that span
  interjection-split turns, downgrade speaker mismatches to warnings when
  the model flagged the labels, and warn on email-shaped close attempts.
  Round-1 prompt text archived in `sample/prompts_r1.md`.

- **2026-09-24, round 2 (core 12 + 12, Sonnet):** flags became
  actionable, the no-acceptance booking came back `none`, sizing traps
  avoided, DNC 3/3. Still wrong: close attempts on bookings counted the
  scheduling back-and-forth after agreement (6 on one call); urgency one
  notch above the selector's read on 5/12 (a generic budget-cycle mention
  scored 4); agreement `strong` where earlier pushback should cap it;
  the fully swapped pitch call was extracted right by content but not
  flagged; case-study exclusion suppressed both genuine anecdotes; one
  payload rejected for an invented nested key; two miscited quotes.
  ≈6.9k/1.1k tokens and $0.025 per booking call at list price.
- **Round 3 (built, not yet run):** speaker-label swap detected and
  corrected in code before prompting (vote-based on rep/prospect
  phrases and header names; catches all 4 known swapped calls, 37/5,156
  bookings and 3/12,819 pitches flagged, mixed evidence left alone);
  close attempts after the agreement turn dropped in code; urgency 4
  requires a decision window for this kind of work and ties resolve
  downward; `strong` requires the prospect to add something and no
  earlier pushback; case-study counts unnamed customer stories; quotes
  must be one span (ellipsis rejected) and a citation off by up to three
  turns is matched and warned rather than failed; unknown keys are
  dropped with a warning instead of rejecting the record (departure from
  the server's extra=forbid, agreed with Tomas). Round-2 prompt text in
  `sample/prompts_r2.md`.

## Open questions

- Urgency: anchors for all five levels (client examples cover three).
- Agreement to meet: is `open` the right middle word?
- Opportunity size: get the qualifiers sheet content; confirm the
  list is open-ended.
- Meat on the bone: built as 0–5 heat in round 4; client to confirm the anchors.
- Opportunity grade: Pipeline stars shown as the interim; 5-star source lands after 2026-10-01 (see Implemented → Opportunity grade).
- Talk share for calls transcribed before utterance `end` was stored
  (round 3): show null, or a word-count approximation labelled as such?
- Client-safe view: exactly which insight fields are rep-facing
  (coaching note, flags?) and hidden from clients.
- Regrade on prompt change: everything, or only rows still on the
  page's default date window?
