---
kind: spec
status: in-progress
area: appointment-emails
updated: 2026-10-06
repos: [udab-server, udab-client]
summary: "Every Hub column but Callback sorts; built on hub-sorting (both repos), scorecard index migration, PROD timings and A/B."
---

# Account Management Hub — sorting on more columns

Client (2026-10-05): "Would love to be able to sort by each column. I'm
on my big screen and can't get everything in one view." Two asks in
one: sortable columns, and a table that does not fit (29 columns on
Appointments, 23 on Pitches). This spec covers the sorts only. Column hiding (Excel-style: hide
what you do not need now, re-enable later, per user) is noted as a
separate feature and deliberately not scoped here (Tomas, 2026-10-05).

## Implemented (2026-10-06, local, unmerged)

Branch `hub-sorting` in udab-server and udab-client (off `upstream/master`
after #792). Option 3 below: every column but Callback, plus the
scorecard index as a migration.

- **Server** (`app/services/appointment_calls.py`): 13 new `SORT_KEYS`
  (`company`, `title`, `meeting_type`, `grade`, `darts`, `urgency`,
  `agreement`, `talk_share`, `heat`, `objection_handling`,
  `objection_count`, `close_attempt_count`, `adherence`).
  `_population_select` gains `JOIN_INSIGHT` and `JOIN_ADHERENCE`
  (one row per task each, joined on `sf_task_sf_id`; adherence only
  when completed). Scorecard sorts reuse the item's subquery through
  `_scorecard_subquery(column, record_type, *extra)`; DARTS points are
  the same CASE sum as `_scorecard_payload`. Picklists rank through
  `_rank()` over `AGREEMENT_ORDER` / `OBJECTION_HANDLING_ORDER`.
- **Nulls last both ways** for the new keys (`NULLS_LAST_SORTS`):
  MySQL puts NULL first ascending, so `order_by()` prepends
  `<key> IS NULL ASC` on asc only (desc is already nulls-last).
- **The key select carries the sort value.** `list_calls` selects the
  sort expressions as `sort_N` labels next to `task_id`, orders the
  inner select by the label and the outer page by `page_keys.sort_N`.
  The outer query therefore never re-evaluates a subquery or needs a
  join the sort referenced (adherence is not joined in `_base_select`;
  ordering the outer by its column would have cross-joined). Alias
  ordering is plan-neutral: timed old vs new on the local seed for
  called_at / account_name / urgency, identical plans and times.
- **Migration** `c7e2a9d4f153`: `ix_sf_quality_scorecard_contact_type_date`
  (`Contact__c, Record_Type_Developer_Name__c, Appt_Date__c`), raw SQL;
  model `__table_args__` updated. Seconds of online DDL.
- **Client**: `SORT_FIELDS` extended, `sortField` on the columns in
  both views; `firstSortDir()` opens scores and dates with the best or
  newest first, names A to Z (the old rule was called_at desc, else asc).
- **Tests**: server 76 passed (`scripts/test.sh tests/test_appointment_calls.py`,
  also `FRESH=1`), 3 new: insight sorts rank and keep missing last,
  pitch heat / objection handling / adherence (failed adherence ignored),
  scorecard / company / title / meeting type; join pruning and keyset
  parity extended to every key and direction. Client 1,613 passed, 2
  new (`firstSortDir`, header click sends desc for Urgency, asc for
  Company). flake8 on the changed files: no new findings.
- Local-only: `scripts/local/explain_appointment_calls.py` (gitignored)
  gained the new sorts.
- Not built: Callback sort (see Options 4), column hiding (separate
  feature, see top).

Open at PR time: run the migration from a local session if Argo is
slow on it (it should not be), then time grade / DARTS on the PROD
reader with the statements in the scratchpad or the explain script.

## Why sorting does not need new indexes here

Since the performance pass (#792, [hub-performance.md](hub-performance.md))
a page is a slim key select over `sf_task` (range on
`ix_sf_task_disposition_created`, bounded by the period filter) with
only the joins the sort needs, filesorted, `LIMIT per_page`, then the
item columns over that page of ids. The sort column always lives on a
joined table, so no index on it can serve the `ORDER BY`; MySQL sorts
the population in memory whatever we do. What matters is that the join
lookup is indexed, and every candidate already is:
`sp_call_insight.sf_task_sf_id`, `sp_call_adherence.sf_task_sf_id`,
`sf_contact.sf_id`, `sf_lead.sf_id`, `sf_quality_scorecard.Contact__c`,
`sp_appointment_email.sf_task_sf_id`. Collations checked on PROD:
`sp_call_adherence.sf_task_sf_id` is `utf8mb4_0900_ai_ci` against
`sf_task.sf_id` `latin1_swedish_ci`; the lookup still uses the index
(sf_task drives, its value is converted).

## Measured on PROD reader (2026-10-05)

Key select only (`EXPLAIN ANALYZE` actual, 50 rows), September 2026 as
a full month vs all time (since 2026-01-01). Population that day:
appointments 6.3k / 50.3k, pitches 22.6k. Compiled from the service
code with the candidate expression added to `order_by`, insight and
adherence as one `LEFT JOIN` on `sf_task_sf_id`, categorical columns
ranked with a `CASE`. Statements and plans: session scratchpad
(`compile_sorts.py`, `prod_plans.txt`); the compile script is worth
folding into `explain_appointment_calls.py` when this is built.

| sort (Appointments) | source | September | all time |
|---|---|---|---|
| called_at (today's default) | sf_task | 30 ms | 282 ms |
| owner_name (exists) | sf_user | 76 ms | 611 ms |
| Company | contact/lead `COALESCE` | 109 ms | 858 ms |
| Title | contact/lead `COALESCE` | 104 ms | 776 ms |
| Urgency | `sp_call_insight.urgency` | 185 ms | 614 ms |
| Agreement to meet | insight, `CASE` rank | 81 ms | 556 ms |
| Rep talk share | `insight.rep_talk_share_pct` | 81 ms | 558 ms |
| Meeting type | newest email, JSON extract | 76 ms | 546 ms |
| Opportunity grade | pipeline scorecard subquery | 486 ms | 3,838 ms |
| DARTS gate | DARTS scorecard subquery, points `CASE` sum | 505 ms | 4,023 ms |

| sort (Pitches) | source | September | all time |
|---|---|---|---|
| called_at (today's default) | sf_task | 95 ms | 930 ms |
| Meat on the bone | `insight.opportunity_heat` | 278 ms | 1,902 ms |
| Objection handling | insight, `CASE` rank | 279 ms | 1,905 ms |
| # objections / close attempts | insight counts | 279 ms | 1,900 ms |
| Talk track adherence | `sp_call_adherence.adherence_pct` | 312 ms | 1,979 ms |
| Callback | next-call self-join on `sf_task` | 2,932 ms | 25,860 ms |

Differences under ≈ 100 ms between insight sorts are cache noise.
Coverage (nulls sort too): 49k completed insights (7k appointment,
43k pitch), 10k completed adherence rows, 6.3k emails with a meeting
type, so most rows are null for these columns and nulls must go last
in both directions.

Why the two slow ones: scorecards are matched by contact and a 60-day
window, and the contact index returns ≈ 24 scorecards per contact to
filter; the Callback subquery walks ≈ 24 tasks per contact on
`ix_sf_task_who_deleted_disposition` and filters on date. Both are per
population row. A `(Contact__c, Record_Type_Developer_Name__c,
Appt_Date__c)` scorecard index would cut the scorecard scan (untested);
a `(WhoId, IsDeleted, CreatedDate)` index on the 19M-row `sf_task` is
≈ 1 GB for a sort nobody asked for by name.

## Scorecard index A/B (local, 2026-10-05)

`ix_sf_quality_scorecard_contact_type_date (Contact__c,
Record_Type_Developer_Name__c, Appt_Date__c)` on the local volume seed
(43k scorecards, 78 % DARTS, 12.6 per contact; August 2026 = 15.5k
appointment rows). Key select, best of 3 warm, `EXPLAIN ANALYZE` actual.
Index created and dropped again afterwards; the dev DB carries no
unmigrated schema.

| query | before | after |
|---|---|---|
| grade, month | 469 ms | 251 ms |
| grade, all time | 1,969 ms | 1,178 ms |
| DARTS, month | 583 ms | 445 ms |
| DARTS, all time | 2,298 ms | 1,856 ms |
| page, default sort, 200 rows, month | 359 ms | 319 ms |
| page, default sort, 200 rows, all time | 718 ms | 734 ms |

Why DARTS gains less locally: the seed writes ≈ 5 DARTS cards per
appointment inside the window, so the index returns 4.8 rows per task
instead of 13.3 (grade: 1.4 instead of 13.3). PROD is more selective
(from the PROD plans): the contact index returns 24.4 rows per task and
the record type + window keeps 0.6 (grade) / 0.7 (DARTS), so the index
should remove ≈ 97 % of the rows read per task on both sorts. Projected,
not measured: all-time grade/DARTS ≈ 4 s → ≈ 1 s, month ≈ 0.5 s →
≈ 0.15 s. The index cannot be tried on the reader (read-only, no DDL);
it has to ship as a migration and be timed on PROD after deploy.
`sf_quality_scorecard` is small (263k rows since 2026-01-01), so the
online DDL is seconds, not the minutes a `sf_task` index would take.

## Options

1. Sort everything but Callback. Code only: `SORT_KEYS`,
   `_sort_expressions`, `SORT_JOINS` gains insight and adherence joins
   in `_population_select`, nulls last; client `sortField` on the
   columns and `SORT_FIELDS`. Scorecard sorts cost 0.5 s on the default
   month, 4 s on an explicit all time (today's all-time pitches count
   is 1.5 s).
2. Insight, contact and email columns only; hold grade, DARTS and
   Callback.
3. As 1 plus the scorecard index (A/B above): recommended.
4. Callback: materialize `next_call_at` on the task row (sweeper) if
   it is ever wanted as a sort.

Decided 2026-10-06 (Tomas): option 3, built as above.
