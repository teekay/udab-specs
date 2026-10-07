---
kind: spec
status: draft
area: appointment-emails
updated: 2026-10-07
repos: [udab-server, udab-client]
summary: "AM fills a Salesmanship card per call in the Hub flyout; written to SF (new record type), mirrored at once, filterable."
---

# Account Management Hub — Salesmanship scorecard

Status: DRAFT 2026-10-07. Salesforce side verified read-only against
the production org (describe, layout, trigger source) and the PROD
replica on 2026-10-07. Decisions below are Tomas's; questions in the
last section are with the client (main contact on vacation).

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
(Score 1 / Score 0 sentences); the Hub form shows the same text.

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
- **Collation (PROD):** `sf_task.Call_ID__c` is `varchar(30)
  latin1_swedish_ci`; `sf_quality_scorecard.Call_ID__c` is
  `varchar(128) utf8mb4_0900_ai_ci`. A correlated match on them cannot
  use an index until the scorecard column is converted (same trap as
  `Contact__c` vs `WhoId`, see NOTES "collation drift"). Values are
  ASCII, so the conversion is lossless.
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
- **Caller is Salesforce's call.** The trigger makes it the contact's
  owner; we do not send it and do not try to force the rep on the
  call. The Hub keeps showing the rep from the Task owner.
- **Link by `Call_ID__c`**, pending the client's answer on the trigger
  (Open questions). Fallback if they want the Task slot untouched:
  leave `Call_ID__c` empty and match by contact + `Appt_Date__c`
  window like DARTS, with `Appt_Date__c` stamped by us.
- Hidden in the client-safe view: it grades the rep.

## Server

### Migration (one, raw SQL)

1. `ALTER TABLE sf_quality_scorecard` add `Professionalism__c`,
   `Client_Knowledge__c`, `Industry_Knowledge__c`,
   `Active_Listening__c`, `Tone_and_Pace__c` (`VARCHAR(128) NULL`) and
   `Salesmanship_Score__c` (`DOUBLE NULL`).
2. `MODIFY Call_ID__c VARCHAR(128) CHARACTER SET latin1 COLLATE
   latin1_swedish_ci NULL` so it matches `sf_task.Call_ID__c`.
3. `CREATE INDEX ix_sf_quality_scorecard_type_call_id ON
   sf_quality_scorecard (Record_Type_Developer_Name__c, Call_ID__c)`.
   Record type first: the subquery always pins the type, and the
   quality team's 95k `Client_Appointment_Scorecard` rows share call
   ids with ours.

Argo does not run migrations (NOTES, 2026-10-07): check the reader's
`alembic_version` after deploy. The new columns are null until the
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
  and it needs no `Appt_Date__c`. (Fallback variant if the client
  rejects `Call_ID__c`: reuse `_scorecard_subquery` with the new type.)
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
   `Call_ID__c = task.Call_ID__c` (decision pending), `Completed_By1__c
   = current user's sfdc_id` when set, the five picklists as `"1"` /
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
  scorecard column aligned to the task's collation (the seed already
  does this for `Contact__c`).
- No real Salesforce locally: the sandbox credentials in `sp_setting`
  fail (`invalid_grant`, 2026-10-07) and the record type must be
  deployed there first. Until then the write path is tested against
  the fake only.

## Deploy checklist

- Run the migration by hand (Argo), verify `alembic_version` on the
  reader.
- Assign "Submit Salesmanship Scorecard" to the AM roles.
- Confirm the trigger decision (below) before the first production
  submit; the `Call_ID__c` line is one constant to flip.
- Sandbox: record type + two new fields deployed, working credentials
  in `sfdc-sandbox-*` settings.

## Open questions (client)

1. **Trigger overwrite.** Does anyone read `Task.Abstrakt_Scorecard_ID__c`
   expecting the quality team's card? If yes, exempt
   `Salesmanship_Scorecard` in `handlerAfterInsert`/`AfterUpdate` (one
   line next to the MDM exemption) and we set `Call_ID__c`. If nobody
   reads it, we set `Call_ID__c` and move on. Only if neither: contact
   + date fallback.
2. **Status / Completed Date.** Is there a reviewer step that closes
   the card, or should a Hub submission close it (`Status__c =
   Closed`, `Completed_Date__c = today`)?
3. **Caller = contact owner** (trigger rule). Acceptable for the
   "which rep was scored" reports on their side?
4. Pitches as well as appointments — confirm.
5. Hide from the client-safe view — confirm.

Side note for the same conversation: the 5-star cards
(`X5_Star_Scorecard`, live since 2026-09-15, stars on 1,066 of 1,087
rows) are not yet read by the Hub's Opportunity grade, which still
looks only at Pipeline cards.
