---
kind: spec
status: in-progress
area: appointment-emails
updated: 2026-10-21
repos: [udab-server, udab-client]
summary: "AM fills a Salesmanship card per call in the Hub flyout; written to SF (new record type), mirrored at once, filterable."
---

# Account Management Hub — Salesmanship scorecard

Status: IN PROGRESS 2026-10-08 — built locally on branch
`salesmanship-scorecard` in both repos (see Implemented), unmerged.
The client's trigger change is live in QAfull, QA and Production
(verified 2026-10-08, see Implemented); merge waits on Tomas's review. Salesforce side verified read-only against the production
org (describe, layout, trigger source) and the PROD replica on
2026-10-07. Remaining questions are with the client (main contact on
vacation) and none blocks development.

Client ask (2026-10-07, condensed): a new "Salesmanship" scorecard
exists in Salesforce. An Account Manager opens an appointment in the
Hub, fills the scorecard there, and the submission goes back to
Salesforce. Not every appointment gets one. The AM must see whether
one is already filled out and what the score was, and filter by it.
Five criteria, each 0 or 1; score = points / 5 as a percent.

## What Salesforce has (verified 2026-10-07)

Everything lives on the object we already mirror, `Quality_Scorecard__c`
(`sf_quality_scorecard`). The scorecard is a **record type**,
`Salesmanship_Scorecard` (label "Salesmanship Scorecard", created
2026-09-25, 0 rows so far). Record type ids differ between orgs;
production is `012Rj000001lYk1IAE`, the sandbox will have its own.

Edit layout for the record type:

| section | field | notes |
|---|---|---|
| Fields | `Contact__c` | the only required field on the object |
| Fields | `Account__c`, `User__c`, `Account_Owner__c`, `Inside_Sales_Manager__c`, `Caller__c` | overwritten by the trigger, see below |
| Fields | `Status__c` | picklist Open / Closed, default Open |
| Fields | `Completed_Date__c`, `Completed_By1__c` (User lookup) | |
| Fields | `Overall_Feedback__c` | long text |
| Salesmanship Criteria | `Industry_Knowledge__c`, `Client_Knowledge__c`, `Professionalism__c` (label "Overall Professionalism") | existing picklists; for this record type only "1" and "0" are active (elsewhere also "2", "NA") |
| Salesmanship Criteria | `Active_Listening__c`, `Tone_and_Pace__c` | new picklists, "1" / "0" |
| (formula) | `Salesmanship_Score__c` | percent, read-only: sum of the five "1"s / 5; help text "5 possible points" |

The per-field help text in Salesforce is the client's rubric verbatim
(Score 1 / Score 0 sentences). The Hub form showed the same text until
the copy revision below.

## Copy revision (proposed 2026-10-19, unmerged)

Client meeting notes (2026-10-19): shorten each criterion's description,
show an example of what right looks like (green, checkmark) and what
wrong looks like (red, X), and make the wrong ones funny. Built on
`update-salesmanship-scorecard-copy` in `udab-client` (the server holds
no rubric text): `SALESMANSHIP_CRITERIA` entries carry `summary`,
`right`, `wrong` instead of `one`/`zero`; the form shows the one-line
summary under the label and the two examples as green/red boxes behind
an "Examples" expand/collapse toggle (client ask 2026-10-21, after
seeing them always visible). Examples are lifted
from the client's HVAC example talk track (Alex at Summit Mechanical,
`Example Talk Track Flow Chart.pdf`, 2026-09-24). The Salesforce help
text is unchanged. Awaiting the client's reaction to a screenshot.

No active validation rules. One active Apex trigger,
`QualityScorecardTrigger` → `QualityScorecardTriggerHandler`:

- **before insert**: for every card sets `Caller__c` = the Contact's
  owner (except the MDM record type), and `Account__c`,
  `Account_Owner__c`, `Quality_Assurance_Owner__c`,
  `Assistant_Partner_Sales_Manager__c`, `Inside_Sales_Manager__c`
  from the Contact's account. Whatever we send in those fields is
  replaced. We send none of them.
- **after insert / after update (when recording tags change)**: for
  every card whose record type name does not start with "Client" and
  that has a `Call_ID__c`, finds Tasks with the same `Call_ID__c` and
  writes the card's id into `Task.Abstrakt_Scorecard_ID__c`. One slot,
  last writer wins, never read before write.

`Call_ID__c` is the dialer's call id (`C-6…`, 10 chars), not a
Salesforce id. On PROD every 2026 call Task carries one, and on
appointment calls since 2026-09-01 the Task slot already points at a
card on 77 % of rows (5,643 of 7,632 at the quality team's
`Non_Appointment_Scorecard`, the rest NAC / Pipeline review / Exec).
Inserting a Salesmanship card with `Call_ID__c` set would overwrite
that pointer; a later quality card would overwrite ours back. The
cards themselves are untouched either way, and the Hub never reads the
Task slot. See Open questions.

## What the mirror has and lacks

- `sf_quality_scorecard` is refreshed by the **daily `sfdc-sync` only
  (≈ 10:00 UTC)**; the hourly `sfdc-stream` type list omits
  `quality_scorecard`. Anything the Hub writes must be upserted into
  the mirror in the same request or it stays invisible for up to a
  day.
- Mirrored already: `Call_ID__c`, `Contact__c`, `Appt_Date__c`,
  `Completed_By1__c`, `Completed_Date__c`, `Status__c`,
  `Overall_Feedback__c`, `Record_Type_Developer_Name__c`, and the
  three formula doubles `Professionalism_Score__c`,
  `Client_Knowledge_Score__c`, `Industry_Knowledge_Score__c` (map
  "1"→1, "0"→0 for this record type).
- Not mirrored: the five raw picklists `Professionalism__c`,
  `Client_Knowledge__c`, `Industry_Knowledge__c`, `Active_Listening__c`,
  `Tone_and_Pace__c`, and `Salesmanship_Score__c`. The sync pulls
  `FIELDS(ALL)` but writes only known columns, so each needs model +
  migration + the two branches of `process_quality_scorecard_record`.
- **Collation (PROD, re-verified 2026-10-12):** `sf_task.Call_ID__c`
  is `varchar(30) latin1_swedish_ci`; `sf_quality_scorecard.Call_ID__c`
  is `varchar(128) utf8mb4_0900_ai_ci`. No conversion is needed: the
  indexed side is the wider charset, so MySQL converts the task's
  latin1 value and probes the scorecard index as-is (measured, see
  NOTES "collation drift"). The spec's first draft converted the column
  anyway; that was dropped on review.
- The old `Salesmanship__c` picklist ("Did the caller close the sale")
  reads "N/A" on all 389k rows and is unrelated.

## Decisions (Tomas, 2026-10-07)

- **Both views** (Appointments and Pitches) for now; scope down if the
  client says so.
- **One Salesmanship card per call.** First submit creates; a later
  submit on the same call patches the same card. The Hub never creates
  a second card for one call.
- **Status and Completed Date are not set**; Salesforce defaults apply
  (card stays "Open"). `Completed_By1__c` is set to the AM's
  Salesforce user so the client can see who scored it (72 of 77 active
  app users have `sp_user.sfdc_id`; without one the field is omitted
  and the card's CreatedBy is the integration user).
- **Caller: send `Caller__c` = Task owner, unconditionally.** Today
  the trigger overwrites it with the contact's owner, so sending it is
  harmless; if the client adds a before-insert exemption (asked
  2026-10-07, pending) it sticks with no code change. The Hub keeps
  showing the rep from the Task owner regardless.
- **Link by `Call_ID__c`.** Client decision (2026-10-07): they do not
  want the Task slot overwritten and will change the trigger
  themselves to exempt `Salesmanship_Scorecard`. We set `Call_ID__c`;
  no contact + date fallback is built.
- Hidden in the client-safe view: it grades the rep.

## Server

### Migration (one, raw SQL)

1. `ALTER TABLE sf_quality_scorecard` add `Professionalism__c`,
   `Client_Knowledge__c`, `Industry_Knowledge__c`,
   `Active_Listening__c`, `Tone_and_Pace__c` (`VARCHAR(128) NULL`) and
   `Salesmanship_Score__c` (`DOUBLE NULL`), each `AFTER` the previous
   one starting at `Salesmanship__c`, `ALGORITHM=INSTANT`.
2. `CREATE INDEX ix_sf_quality_scorecard_type_call_id ON
   sf_quality_scorecard (Record_Type_Developer_Name__c, Call_ID__c)`.
   Record type first: the subquery always pins the type, and the
   quality team's 95k `Client_Appointment_Scorecard` rows share call
   ids with ours.

Since udab-server #799 (merged 2026-10-09) the prod workflow runs
`alembic upgrade head` in a k8s Job and only deploys the
`prod-migrated-<sha>` image, so the migration runs itself; still
check the reader's `alembic_version` after deploy. The new columns are null until the
next daily sync; they only matter for Salesmanship rows, which the Hub
writes through itself, so no one-time re-sync is needed.

### Model and sync

`app/models/sf_quality_scorecard.py`: the six columns + the index.
`app/commands/sfdc/sync.py` `process_quality_scorecard_record`: the six
assignments in both the update and the insert branch. Move that
function (or a thin `upsert_quality_scorecard(db, record)`) into a
service module so the route below can import it without pulling the
Typer command.

### Record type id

Resolve by developer name, not a constant: `SELECT Id FROM RecordType
WHERE SobjectType='Quality_Scorecard__c' AND
DeveloperName='Salesmanship_Scorecard'`, cached per process. The
hard-coded record type ids on `Sfdc` are production ids and would be
wrong in the sandbox.

### Read side (`app/services/appointment_calls.py`)

- `SCORECARD_TYPE_SALESMANSHIP = "Salesmanship_Scorecard"`.
- `_salesmanship_subquery(column)`: newest card (`id DESC`) with
  `Record_Type_Developer_Name__c = Salesmanship_Scorecard AND
  Call_ID__c = SfTask.Call_ID__c`. Not the contact+window rule: exact,
  and it needs no `Appt_Date__c`.
- `_base_select` gains `salesmanship_scorecard_id`; `_items_for_rows`
  loads it through `_scorecards_by_id` like the other two; item field
  `salesmanship` (schema `AppointmentCallSalesmanship`):

  ```
  sf_id, points (0–5), max_points 5, score_pct,
  criteria: [{key, label, value: 1|0|null}] × 5 in rubric order,
  feedback, completed_by_sf_id, created_at, last_modified_at
  ```

  `points` is computed from the five picklists in Python (`"1"` → 1),
  `score_pct` from `Salesmanship_Score__c` when present else
  `points / 5 * 100`.
- Sort `salesmanship` (append to `SORT_KEYS`, so it lands in
  `NULLS_LAST_SORTS`): the subquery over a `CASE` sum of the five
  picklists, same shape as `_darts_points_expr()`. Client opens it
  descending.
- Filter `salesmanship` (query param, one token): `none` → `NOT
  EXISTS`, `scored` → `EXISTS`, `0`…`5` → `EXISTS … AND points = n`.
  Criteria go on the population select like the others, so count and
  keys both honour it; cost is the indexed lookup per population row,
  the same class as the DARTS sort (PROD all time < 1 s after its
  index).
- `KIND_LABELS`/export: the transcript export header gains no field
  (not asked).

### Write route

`PUT /appointment-calls/{sf_task_sf_id}/salesmanship-scorecard`,
permission **`SUBMIT_SALESMANSHIP_SCORECARD = 'Submit Salesmanship
Scorecard'`** (new constant; AM roles get it at deploy like
"Appointment Calls"). Writer session (`get_async_db`), `Sfdc` via a
`get_sfdc` dependency as in `routes/extension.py`.

Body (all five required, feedback optional):

```
{ "professionalism": 0|1, "client_knowledge": 0|1, "industry_knowledge": 0|1,
  "active_listening": 0|1, "tone_and_pace": 0|1, "feedback": "…" }
```

Steps:

1. `get_call(db, id)`; 404 outside the population (the population is
   authoritative, as for export).
2. `WhoId` must be a Contact (`003…`); a Lead-only call gets 409
   "scorecards need a Salesforce contact" (`Contact__c` is a Contact
   lookup and required).
3. If the item already has `salesmanship.sf_id`: `patch_object` the
   five picklists (+ `Overall_Feedback__c`). Else `post_object`
   `Quality_Scorecard__c` with `RecordTypeId`, `Contact__c = WhoId`,
   `Call_ID__c = task.Call_ID__c`, `Caller__c = task.OwnerId`,
   `Completed_By1__c = current user's sfdc_id` when set, the five picklists as `"1"` /
   `"0"` strings, `Overall_Feedback__c`.
4. Re-read the record from Salesforce (`SELECT FIELDS(ALL) FROM
   Quality_Scorecard__c WHERE Id = …`, so formula and trigger-set
   fields come back) and upsert it into `sf_quality_scorecard` with
   the shared upsert. Commit.
5. Return the refreshed item (`get_call` again, same shape as GET), so
   the flyout and the row update without a second request.

Salesforce failure → 502 with the Salesforce message; nothing is
written locally. Picklist values are strings in Salesforce; send
`"1"`/`"0"`, never integers. Log the write with the app logger
(`action`, task id, card id, user email) like other Salesforce writes.

## Client

- **Column** `salesmanship` ("Salesmanship", sub "AM scorecard") in
  both views after DARTS, `sortField: 'salesmanship'`, opens
  descending (`firstSortDir`). Cell: `4 / 5` with a badge coloured by
  points (5 green, 3–4 amber, 0–2 red) and a tooltip listing the five
  criteria; empty → "Not scored" (no "yet": nothing scores it
  automatically). Add to `CLIENT_SAFE_HIDDEN_KEYS`.
- **Filter** in `AppointmentCallFilterBar.vue`: a `ReportSelect`
  "Salesmanship" in place of one "Coming soon" slot, options Any /
  Not scored / Scored / 5 of 5 … 0 of 5, mapped to the `salesmanship`
  param in `buildListParams` (`constants/appointment-calls.js`),
  persisted with the other filters in view preferences.
- **Flyout**: new section `scorecard` ("Scorecard", `ti
  ti-clipboard-check`) between Insights and Transcript, hidden in the
  client-safe view. Two states:
  - *Card exists*: the five criteria as rows (label, 1/0 badge, rubric
    help text collapsed under a "?" toggle), the score, feedback,
    "Scored by … on …". "Edit" button when the user has the
    permission; editing reuses the form pre-filled.
  - *No card*: the form when the user has the permission, otherwise
    "Not scored". Five rows, each label + the Score 1 / Score 0 help
    text + a Yes/No radio pair; optional feedback textarea; Submit is
    disabled until all five are answered. On success the flyout calls
    the same path as `loadDetail` (`detail.value = response;
    emit('updated', response)`) so `applyDetailToRow` refreshes the
    row and column. Toast on Salesforce error, form stays filled.
- `helpers/udab-api.js`: `submitAppointmentCallSalesmanship(id, body)`
  → `put('/appointment-calls/${id}/salesmanship-scorecard', body)`.
- Bootstrap CSS only: radios and the section are plain markup, no
  modal.

## Tests and local data

- Server: `tests/test_appointment_calls.py` — `_scorecard` helper
  takes `Call_ID__c`; cases for the column (newest card wins, other
  record types with the same call id are ignored), the filter tokens,
  the sort with nulls last, and the route with `get_sfdc` overridden
  by a fake (create path, patch path, Lead 409, Salesforce error 502,
  mirror row present after the call). Through `scripts/test.sh`.
- Client: vitest for the column cell, the filter param mapping, and
  the flyout form (disabled until complete, submit payload, `updated`
  emitted).
- `scripts/local/seed_appointment_calls_enrich.py`: Salesmanship
  cards on a fraction of seeded calls, with `Call_ID__c` set and the
  local `sf_task.Call_ID__c` given PROD's latin1 definition.
- No real Salesforce locally: the sandbox credentials in `sp_setting`
  fail (`invalid_grant`, 2026-10-07) and the record type must be
  deployed there first. Until then the write path is tested against
  the fake only.

## Deploy checklist

- Migration `a1c3e5f7b9d2` revises `72b100c2aa1e` (rebased onto
  upstream master 2026-10-12). CI runs it (#799); verify
  `alembic_version` on the reader after deploy.
- Assign "Submit Salesmanship Scorecard" to the AM roles.
- ~~Client deploys the trigger change first~~ — live in QAfull, QA and
  prod since 2026-10-08 (verified on the sandbox round-trip).
- Sandbox: record type + two new fields deployed, working credentials
  in `sfdc-sandbox-*` settings.

## Implemented (2026-10-08, local, unmerged)

Branch `salesmanship-scorecard` in `udab-server` and `udab-client`.

**Salesforce side, verified read-only on production 2026-10-08:** the
client's developer changed `QualityScorecardTriggerHandler`
(modified 2026-10-08 17:00 UTC). `NON_STAMPING_RECORD_TYPES` holds
`Salesmanship_Scorecard`; a Salesmanship card with a `Call_ID__c` now
fills the new `Task.Salesmanship_Scorecard_ID__c` and leaves
`Abstrakt_Scorecard_ID__c` alone. The before-insert branch (Caller =
contact owner) is unchanged. Not mirrored on `sf_task`: the Hub matches
on `Call_ID__c` and needs no Task back-pointer.

**Server** (`udab-server`):

- Migration `a1c3e5f7b9d2` (revises `72b100c2aa1e`): six columns on `sf_quality_scorecard`
  (`Professionalism__c`, `Client_Knowledge__c`, `Industry_Knowledge__c`,
  `Active_Listening__c`, `Tone_and_Pace__c` VARCHAR(128);
  `Salesmanship_Score__c` DOUBLE, appended after `Salesmanship__c`,
  `ALGORITHM=INSTANT`), index
  `ix_sf_quality_scorecard_type_call_id (Record_Type_Developer_Name__c,
  Call_ID__c)`. Model and both branches of
  `process_quality_scorecard_record` carry the columns.
- `app/services/appointment_calls.py`: `SCORECARD_TYPE_SALESMANSHIP`,
  `SALESMANSHIP_CRITERIA` (rubric order), `_salesmanship_newest_id`
  (`MAX(id)` over the `(type, Call_ID__c)` index; see NOTES "planner
  trap" for why not `ORDER BY … LIMIT 1`) and `_salesmanship_subquery`
  (a PK lookup on it),
  `_salesmanship_filter` (`none` / `scored` / `0`–`5` as `EXISTS`),
  sort key `salesmanship` (nulls last), item field `salesmanship`
  (`_salesmanship_payload`). `ListFilters.salesmanship` and the
  `salesmanship` query param on the list route.
- `app/services/salesmanship_scorecard.py`: `submit()` — population
  check (404), Contact-only (409 on a Lead), call id required (409),
  create (`post_object` with record type resolved by developer name,
  cached per API host; `Contact__c`, `Call_ID__c`, `Caller__c` = task
  owner, `Completed_By1__c` = the user's `sfdc_id` when set, the five
  picklists as `"1"`/`"0"`, `Overall_Feedback__c`) or patch of the
  existing card; then `SELECT FIELDS(ALL) … WHERE Id` and the sync's
  upsert, so the mirror row carries the formula and trigger-set
  fields. `PUT /appointment-calls/{id}/salesmanship-scorecard` with
  permission `SUBMIT_SALESMANSHIP_SCORECARD` ('Submit Salesmanship
  Scorecard'), writer session, `get_sfdc` dependency; any Salesforce
  failure → 502, nothing written locally.
- Tests: four new cases in `tests/test_appointment_calls.py` (item
  payload and newest-wins, filter and sort, create-then-patch through
  a `FakeSfdc` with the mirror row asserted, refusals 403/404/409/422/
  502). 81 pass through `scripts/test.sh`; sync tests unchanged.
- `scripts/local/seed_appointment_calls_enrich.py`: stamps
  `Call_ID__c` on seeded tasks, adds Salesmanship cards
  (`--salesmanship-share`, default 0.25), gives the local
  `sf_task.Call_ID__c` PROD's latin1 definition (local drift only; the
  scorecard column stays utf8mb4 as on PROD).
  `explain_appointment_calls.py` gained the four Salesmanship cases
  and `--only <label text>`.

**Client** (`udab-client`):

- `constants/appointment-calls.js`: `SALESMANSHIP_CRITERIA` (labels +
  the Score 1 / Score 0 help text), `SALESMANSHIP_FILTER_OPTIONS`,
  `salesmanshipBadgeClass/Empty/Title`, filter `salesmanship` (default
  `any`) through `buildListParams`, the preferences payload and
  sanitizer; sort field; `CLIENT_SAFE_HIDDEN_KEYS` += `salesmanship`.
- Column "Salesmanship / AM scorecard" after DARTS (Appointments) and
  after Flags (Pitches): `points / 5` badge, tooltip with the five
  answers, "Not scored" otherwise.
- Filter bar: "Salesmanship" select after Industry.
- Flyout: section `scorecard` between Insights and Transcript, hidden
  in the client-safe view (nav link too). Component
  `AppointmentCallSalesmanship.vue`: read view (marks per criterion,
  feedback, scored date, Edit for holders of the permission) and the
  form (Yes · 1 / No · 0 toggle groups, collapsible scoring guide per
  criterion, optional feedback, submit disabled until all five are
  answered; success toast + `saved` → flyout `detail` and the row;
  error toast keeps the form open). API helper
  `submitAppointmentCallSalesmanship`.
- Tests: component (3), flyout section + permission + client-safe,
  page column cells, filter bar label, constants. 1,633 pass.

**Verified in the browser (local stack, 2026-10-08):** column, filter
("Scored" → 1,534 of 7,143 seeded calls), flyout section, Edit
pre-fill, and a submit that reached the server and came back as a
toast "Salesforce rejected the scorecard: invalid_grant" with the form
intact at the time (stale sandbox credentials).

**Verified against the `qafull` sandbox (2026-10-08, after Tomas
renewed the sandbox password):** the sandbox task `00TU900000LpW9pMAF`
(Lallier Construction, call `C-72665894`) was mirrored locally with
`sfdc-sync --type … --id …`; `PUT …/salesmanship-scorecard` created
card `a11U900000AURujIAH` (8.6 s round-trip) and a second PUT patched
the same card (4.2 s). Read back from the sandbox: all five picklists,
`Salesmanship_Score__c` 100, `Completed_By1__c` = the scorer's SF
user, `Call_ID__c` set; the Task got `Salesmanship_Scorecard_ID__c` =
the card and kept `Abstrakt_Scorecard_ID__c` (the quality robot's Exec
card). The trigger replaced `Caller__c` with the contact's owner
(Nicholas Moehlenkamp) although the Task owner is Nicholas Wulf — the
exact mis-credit case raised with the client. The mirror row matched
the sandbox within the request. The test card was left in the sandbox. Local timings on the
seeded all-time population (91k rows, 19.5k cards): filters `scored`
/ `none` / `5` ≈ 0.52 s each against a 0.50 s default page; sort
≈ 1.7 s; plans use `ix_sf_quality_scorecard_type_call_id`.

## Open questions (client)

1. ~~Trigger overwrite~~ — answered 2026-10-07: the client keeps the
   Task slot for the quality team's card and changes the trigger to
   exempt the Salesmanship record type. Tracked in the deploy
   checklist.
2. ~~Status / Completed Date~~ — answered 2026-10-12: the existing SF
   automations stay as they are; the Hub leaves the card Open and does
   not set `Completed_Date__c`.
3. ~~Caller = contact owner~~ — answered 2026-10-12: no further trigger
   change. The before-insert handler keeps replacing the `Caller__c`
   we send (the Task owner) with the contact's owner; the Hub still
   sends it so a later exemption needs no code change.
4. Pitches as well as appointments — shipped as built (both views),
   not contested.
5. Hide from the client-safe view — shipped as built, not contested.

Client confirmed 2026-10-12: ready to merge and deploy.

Side note for the same conversation: the 5-star cards
(`X5_Star_Scorecard`, live since 2026-09-15, stars on 1,066 of 1,087
rows) are not yet read by the Hub's Opportunity grade, which still
looks only at Pipeline cards.
