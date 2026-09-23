---
kind: spec
status: draft
area: appointment-emails
updated: 2026-09-23
repos: [udab-server, udab-client]
summary: "Sales Enablement calls in the call queue: third tab over a rep-department population, no client account, own columns."
---

# Call Queue — Sales Enablement tab

Status: DRAFT 2026-09-23 — prod data checked (read-only reader,
`sapper` DB). Q1–Q3, Q5–Q8 decided by Tomas 2026-09-23 (see Decided);
**Q4 (what an SE call *is*) stays open** — Tomas takes it to the client
2026-09-24 and it decides whether the Kind labels apply on the new tab.
Q7 columns are expected to move after client feedback. Builds on
[call-queue.md](call-queue.md) (shipped) and
[call-queue-round-2.md](call-queue-round-2.md); assumes rounds 3 and 4
(consoles, "Download all") land first, since both touch the same
population helper and export pipeline.

Client ask (2026-09-23, verbatim, relayed by Tomas):

> I am going to confirm with them tomorrow. First can you please make
> sure we are able to filter for these individuals from the
> appointment/pitch queue UI?
>
> They are sales enablement, so they won't have every parameter the
> same (for example: we are setting appointments for our internal team
> so no appointment email goes out)
>
> You can put them all under 'reps'
>
> Sales Enablement: Nathan Furrow, Ava Jansen, Daniel Beene, Dylan
> Scott, Nycole Merced, Shawn Bailey, Stephen Bendler, Tyler
> Reed-Ingram, Seth Rothermich, Andrew Fuhrman, Brennan Redinger, Dan
> Monroe, Daniel Petty, Henry Trigg, Joshua Mortel, Keaton Osterhage

The literal ask is "put them under Reps". This spec argues for a
**third tab instead** (see Why a tab) and lists the questions that
decide between the two.

## What prod says (2026-09-23)

Source: prod Aurora reader, `sapper` schema (`sf_user`, `sf_task`,
`sf_account`). Window = the queue's `APPOINTMENT_CALLS_SINCE`
(2026-08-25 13:35 UTC) to 2026-09-22.

**Why the reps are missing today.** The Rep dropdown
(`filter_options()`) is the distinct set of Task owners *inside the
population*; it has no rep-level rule at all. The population requires
the Task's account to be Pipeline Client **and `Status__c = 'Active'`**
(`population_filters()`), with an inner join to `sf_account`. Sales
Enablement sells Abstrakt's own service, so their accounts are never
Active clients — every one of their calls fails that rule.

**The 16 users.** All exist in `sf_user`. 15 have
`Department = 'Sales Enablement'`; Daniel Beene is `Corporate
Marketing` (title: Director of Employee Engagement and Corporate
Culture). Titles are "Market Development Manager" (± "- Nurture",
"- Referrals") or "Director of Sales Enablement". `Partner_Sales_Team__c`
is populated on all 15: Revenue Raptors, The Titans, MLS. (This
corrects call-queue.md's note that `sf_user` lacks the team field.)

**Three have no calls in the window**: Nathan Furrow, Seth Rothermich
(both Directors), Daniel Beene. A call-derived dropdown will never list
them (Q3).

**Their appointment/pitch calls** (dispositions in the two buckets,
`TaskSubtype = Call`, not deleted, in window) — 847 calls, 13 reps:

| Account bucket | Calls | Reps |
|---|---|---|
| Pipeline Client, `Prospect` | 581 | 13 |
| No account — `WhoId` is a Lead (`00Q…`) | 260 | 10 |
| Pipeline Client, Implementing / Signing / Canceled | 7 | |
| Pipeline Client, `Active` | **0** | |

Recording: 639 / 847 carry `Call_Recording_URL_Public__c` (≈ 75 %;
the current population is ≈ 95 %). Dispositions are the normal ones
("KDM Pitched" 494, "Appointment" 311, "Appointment Confirmation" 21,
follow-ups ~20). Subjects are Orum-stamped like everyone else's.

**Today's dropdown**: 253 reps — Fulfillment 192, Account Management
54, Sales 4, others 3. Nobody from Sales Enablement.

**Blast radius of simply dropping the status rule** (all departments,
in window): +357 Prospect, +308 Canceled, +145 On Hold Pipeline Client
calls and +112 Lead-only calls from 49 *other* reps. That changes the
Fulfillment queue nobody asked to change — hence a scoped population,
not a relaxed rule.

**Transcription**: per Tomas 2026-09-23 these calls are already being
transcribed (separate conversation; Dani folded the scope change into
the poller). Not verified in this checkout — the reader only exposes
`sapper`, not the udab schema (`sp_call_transcript`). The queue's
transcript-state logic is population-agnostic, so nothing changes
there; confirm at implementation that the poller's scope matches Q2's
answer, or SE rows will sit at `pending` forever.

## Why a tab, not a Rep group

The literal ask ("put them under reps") would fold a second population
behind a filter that was built to narrow one population:

- **The Appointments columns come from the appointment-email
  pipeline.** Meeting date, meeting type, building, and the flyout's
  Meeting tab are read off the email context. SE never sends that
  email (their own words), so every one of those cells is blank.
- **"Client" means the wrong thing.** Today it is the Pipeline Client
  the rep calls *for*. For SE it is the prospect's own company
  (Prospect status), or nothing (260 Lead-only calls).
- **Account-derived filters go dark.** Account owner, Team and
  Industry are read off the account and *its* owner; Lead-only calls
  have neither. SE's meaningful team is the rep's own
  `Partner_Sales_Team__c`.
- **It pollutes the Fulfillment view.** Managers who use the page
  today would see Abstrakt's own pipeline in their totals and "Download
  all" unless they remember to exclude it.

A tab is already the page's unit for "a population with its own
columns" (`VIEWS` in `constants/appointment-calls.js`; round 3 added
per-view placeholder filters). The two existing tabs share one
population and differ by `kinds`; the new one differs by population.
Rep filter on the SE tab then lists only SE reps for free.

## Decided (Tomas, 2026-09-23)

- [x] **Q1. A tab**, "Sales Enablement", next to Appointments / Pitches.
- [x] **Q2. Department.** `sf_user.Department = 'Sales Enablement'` —
      15 of the 16, self-maintaining as people join/leave. Excludes
      Daniel Beene (Corporate Marketing; zero calls in the window
      anyway). Rejected: the three team values (moving target) and the
      16 names (business data in code/config drifts on the first hire).
- [x] **Q3. Rep dropdown stays call-derived.** Only reps with calls
      appear; a rep with none would just produce an empty list.
      Furrow, Rothermich, Beene are therefore absent until they call.
- [x] **Q5. Lead-only calls are in** (260 of 847, 31 %); company comes
      from `Lead.Company`.
- [x] **Q6. Same permission** (`View Appointment Calls`). No new
      permission for a tab.
- [x] **Q7. Columns as proposed** — Company (prospect's), Prospect,
      Title, Kind, Call date, Duration, Rep, Rep team, Transcript.
      Dropped: Client, Meeting date, Meeting type, Building, Account
      owner, Industry. Expected to move after client feedback; the
      meeting-date question (do SE use `Lead.Appt_Scheduled_Date__c /
      _Time__c / _Timezone__c`?) rides along with Q4.
- [x] **Q8. Export columns match the display columns.** The SE `.txt`
      header block is the SE column set, not the client one — see
      Design / Export.

## Open — for the client (2026-09-24)

- [ ] **Q4. What is a Sales Enablement call?** Tomas's read: SE calls
      are a *different thing* from appointment/pitch calls. Claude's
      read from prod: they are the *same call types* made in a
      different business context. Evidence: in the window the 13
      active reps logged "Appointment" 311, "Appointment Confirmation"
      21, "KDM Pitched" 494 and ~25 follow-ups — the shared
      disposition vocabulary, no SE-only disposition; their other
      ~20 k calls are the usual No Operator / Gatekeeper / voicemail
      outcomes. The client's own words ("setting appointments for our
      internal team so no appointment email goes out") read as: the
      appointment goes to an Abstrakt AE and the attendee is a company
      Abstrakt wants to sign, so the *client slot is Abstrakt itself*
      — which is exactly why their accounts are Prospect-status or
      bare Leads. On that reading, "Sales Enablement" is **who**, and
      Kind is still **what**: one tab over a second population, Kind
      column/filter inside it (all six kinds occur in their data).

      **Ask the client:** is an SE "Appointment" an appointment booked
      for an Abstrakt account executive with the prospect company as
      attendee? If yes, the Kind labels apply unchanged and the design
      below stands. If an SE appointment is something else (e.g. a
      demo the SE rep runs themselves), the Kind labels are wrong for
      that tab and it needs its own label set — a small change, but
      it is the one that hinges on the answer. Also ask where the
      meeting date lives for SE appointments (Q7).

      Decision here does **not** reopen Q1: either way the calls do
      not belong in the Appointments/Pitches tabs (blank email-derived
      columns, wrong "Client").

## Leans (Tomas + Claude, 2026-09-23; settle at implementation)

- *(lean)* **Population selector on the API**, not a new router. One
  query param `population=client | sales_enablement` (default
  `client`) on the list, `filters`, `export` (selected rows) and
  `exports` (Download all) endpoints. Round 4's Batch job passes the
  list params through, so it inherits the selector with no job change.
- *(lean)* **The department string is a code constant** next to
  `PIPELINE_CLIENT`, not `sp_setting` (site-wide config only) and not
  a name list. If Q2 lands on teams instead, same shape, different
  column.
- *(lean)* **No new kinds, no new dispositions.** Same six kinds, same
  `CALL_DISPOSITION_MAP`. The two lowercase "kdm pitch" tasks in their
  data are not worth a variant.
- *(lean)* **No migration.** Everything is derivable from mirrored
  columns already synced (`sf_user.Department`,
  `sf_user.Partner_Sales_Team__c`, `sf_lead.Company`).
- *(lean)* **Flyout hides the Meeting tab** on the SE population
  (there is no email context to show); header shows Company instead of
  account name. Transcript / summary tabs unchanged.

## Design

### udab-server — `app/services/appointment_calls.py`

**Population.** `population_filters()` grows a `population` argument;
the route validates it against `POPULATIONS = ("client",
"sales_enablement")` and passes it into `ListFilters`.

```
client (today, unchanged):
  disposition IN buckets, TaskSubtype = Call, not deleted, since,
  account.RecordTypeId = PIPELINE_CLIENT AND account.Status__c = 'Active'
sales_enablement:
  disposition IN buckets, TaskSubtype = Call, not deleted, since,
  owner.Department = SALES_ENABLEMENT_DEPARTMENT        -- Q2
```

`_base_select()` / `_population_count_select()`: the `sf_account` join
becomes an **outer join** (Lead-only rows, Q5). The `client`
population's `RecordTypeId`/`Status__c` predicates keep it effectively
inner. `Owner` is already an outer-joined alias; for `sales_enablement`
the Department predicate makes it effectively inner.

Index check: the SE branch is still driven by
`ix_sf_task_disposition_created`; the owner predicate is applied on the
joined `sf_user` row (small table, indexed by PK). No new index. Verify
with `EXPLAIN` on prod volume at implementation (the client population
runs against 25 k rows; SE against < 1 k).

**Item shape** — two labels change meaning; the JSON keys stay so the
client's column renderers need no branching on the wire (the export
header does branch, by population — Q8):

- `account.name` → `COALESCE(sf_account.Name, sf_lead.Company)`
  (company). `account.sf_id` stays the account id or null.
- `team` → `Owner.Partner_Sales_Team__c` for `sales_enablement`,
  `AccountOwner.Partner_Sales_Team__c` for `client` (a `CASE` on the
  population, or two select variants — implementer's choice).
- `industry`, `am` (account owner), `appointment_email`: null for SE
  rows (naturally, via the outer join / no email).

**Filters** (`ListFilters`): same fields. `teams` filters on the same
column the item's `team` comes from. `account_ids`,
`account_owner_ids`, `industries` are accepted and simply match the
outer-joined columns (the SE UI never sends them). `search` gains
`sf_lead.Company` in its OR list so Lead-only rows are findable by
company.

**Sort**: `account_name` sorts the `COALESCE` expression, so the
Company column sorts on the SE tab. Other keys unchanged.

**`filter_options(population)`**: `owners`, `teams`, `kinds` populated
from the selected population; `accounts`, `account_owners`,
`industries` returned empty for `sales_enablement` (the UI hides the
clusters; keeping the keys keeps the response model).

**Export** (Q8: header = display columns): `export_header(item,
population)` emits the SE column set for `sales_enablement` rows —
Company / Prospect (name — title) / Kind / Called / Duration / Rep /
Team / SF Task — and the existing block for `client`. Still one
`Label:  value` line per column, every line always present, so each
population's block is positionally stable on its own.
`export_zip_name()` unchanged. The Download-all job (`run_export`)
receives `population` through the existing params dict and passes it
to the header.

### udab-client

- `constants/appointment-calls.js`: `VIEWS` gains `{ value:
  'sales_enablement', label: 'Sales Enablement' }`;
  `KIND_VALUES_BY_VIEW.sales_enablement` = all six kinds;
  `buildListParams` / `buildExportParams` add `population` derived
  from the view (`sales_enablement` → `sales_enablement`, else
  `client`). `sanitizeViewPreferences` accepts the new view value.
- `AppointmentCallsPage.vue`: `SALES_ENABLEMENT_COLUMNS` = Company
  (renders `account.name`, sorts `account_name`), Prospect, Title,
  Kind, Call date, Duration, Rep (owner only — no "and owner"), Team,
  Transcript. `activeColumns` picks by view. `viewNoun` →
  "Sales Enablement calls".
- `AppointmentCallFilterBar.vue`: on the SE view hide the Account,
  Account owner and Industry clusters (extend the per-view placeholder
  mechanism from round 3 rather than a new prop); Team cluster label
  stays "Team" (it is the rep's team here — the empty account-owner
  cluster removes the ambiguity). Search placeholder: "Company,
  prospect or rep". `filters` endpoint called with `population`.
- `AppointmentCallFlyout.vue`: Meeting tab hidden when
  `appointment_email` is null **and** the view is SE (an Appointments
  row without an email still shows the tab's empty state today — keep
  that).
- Switching to/from the SE tab clears `filters.kinds` like today and
  additionally clears the account-derived selections (they cannot
  match).

## Out of scope

- Relaxing the client population's `Active` rule (the +900-call blast
  radius above). If the client wants Prospect/Canceled calls for
  Fulfillment reps, that is its own ask.
- A user-derived Rep dropdown (Q3 = yes would reopen this).
- Meeting date / building for SE appointments (Q7 — only if a source
  exists).
- Poller scope. Owned by Dani; this spec only depends on it matching
  Q2.
- Naming/permission changes beyond the tab label.

## Implementation plan (after Q&A)

1. **udab-server**: `POPULATIONS`, `SALES_ENABLEMENT_DEPARTMENT`,
   `population_filters(population)`, outer-join `sf_account`, item
   `team`/`account.name` expressions, `search` over `Lead.Company`,
   `filter_options(population)`, `population` on the four routes and
   `ListFilters.from_query`. Tests: population separation (an SE
   Prospect call is absent from `client`, present in
   `sales_enablement`; a Fulfillment Active call the reverse), Lead-only
   row renders company and null account id, `filter_options` for SE
   lists only SE owners, export header for an SE row.
2. **udab-client**: constants, columns, filter bar clusters, flyout
   Meeting tab, preference sanitizer. Vitest for `buildListParams`
   population mapping and the sanitizer.
3. `EXPLAIN` both populations on the prod reader before the PR.
4. Land after round 4 (`btn-download-all-transcripts`) merges; rebase
   the population helper changes on top of it.
