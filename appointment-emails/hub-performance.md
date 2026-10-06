---
kind: spec
status: done
area: appointment-emails
updated: 2026-10-06
repos: [udab-server, udab-client]
summary: "Hub load time: every page-load query timed on PROD, rewrites timed on the replica, covering index A/B done locally."
---

# Account Management Hub — page-load performance

Status: DONE — shipped as udab-server #792 and udab-client #352 (status corrected 2026-10-06; the line used to say "built locally, unmerged"). See Implemented below. Analysis follows. Every number
below was measured on 2026-10-01: PROD numbers on the Aurora reader
(`cluster-ro`, 8.0.42, 42 GB buffer pool) with the exact SQL the
routes compile (binds inlined via `scripts/local/explain_appointment_calls.py`'s
`literal()`), index experiments on the local volume seed. "Server"
times are `EXPLAIN ANALYZE` actuals; "wall" times include the VPN
round trip and result transfer and overstate what the API sees.

## Implemented (2026-10-01, local, unmerged)

Branch `hub-performance` in udab-server; no migration. (The client
change came on 2026-10-02, see "Built 2026-10-02".) Recommendations 1–4 below; the covering index (5) was left
out on purpose. Code map in [NOTES.md](NOTES.md) ("Hub query shape
after the performance pass").

- `list_calls`: id select (`_population_select` with only the joins
  the filters and sort need) sorted + limited, then `_base_select(keys)`
  joins the item columns and subqueries onto the page of ids.
- Count: same slim select, `filters.joins()` only.
- `filter_options`: one `GROUP BY AccountId, OwnerId` pass over the task
  side of the population + account rule and name lookups on the result;
  5-minute per-process cache (`reset_filter_options_cache()` for tests).
- GET routes on `get_async_readonly_db`; the export POST stays on the
  writer.
- Local tooling: `seed_appointment_calls_enrich.py` (scorecards,
  adherence, insights on the volume seed; aligns the local scorecard
  collation, see NOTES), `explain_appointment_calls.py` updated.

Verification (local stack, volume seed + enrich: 82,645 population
rows, 43k scorecards, 3k adherence rows, 6k insights):

- **Row-for-row equivalence**: the old implementation's SQL for 76
  (filters × sort × direction × page) combinations was compiled before
  the change and run against the new statements on the same data —
  identical rows and order in all 76; counts identical. Old total
  1,659 s, new 44 s.
- **Tests**: `scripts/test.sh tests/test_appointment_calls.py` — 72
  passed (6 new: join pruning per filter/sort, keyset item parity under
  non-date sorts, cache until reset, cache bypass + case-insensitive
  option order).
- **Local API** (uvicorn, admin token, warm):

| request | local before | local after |
|---|---|---|
| `/filters` | ≈ 5 × 0.65 s | 0.78 s cold, **0.01 s cached** |
| list, appointments, default | 0.5 s query | 1.2 s end-to-end |
| list, pitches, default | | 0.8 s |
| list, appointments, sort account_name | 137 s (query) | 1.2 s |
| list, appointments, sort transcript_state | | 2.0 s |
| list, pitches, sort owner_name, 200/page | 55 s (query, account sort) | 1.2 s |

PROD projections stay the ones measured on the replica with the same
rewrites (table above): filters 19 s → ≈ 1.2 s uncached, counts
−40–45 %, non-date sorts 8–57 s → 0.5–2 s.

## First paint, measured (2026-10-02) — correction

The pass above fixes the slow sorts, `/filters` and the counts. It does
**not** materially change the first paint of the default view, and the
2026-10-01 write-up did not say so clearly. Browser trace of the local
page (appointments, `called_at` desc, last month, 200 rows/page; rows
painted at 2.9 s), same result on master and on the branch:

| phase | time | note |
|---|---|---|
| app boot | 0.17 s | cached JS |
| `GET /view-preferences` | 0.51 s | the list waits for it; 5 ms direct, the rest is the local tunnel |
| `GET /appointment-calls` | 1.75 s | server 0.46 s (master 0.50 s); ≈ 1.3 s tunnel latency + 458 KB uncompressed body |
| parse + render 200 rows | 0.49 s | two long tasks; 31 columns × 200 rows, 14 `AppointmentCallInsightCell` instances per row |

Server stages for that request (branch): count 48 ms, page query
324 ms, six per-page loaders ≈ 40 ms, item build ≈ 20 ms, validate +
serialize 9 ms. Body: 458 KB, 2.3 KB/row, gzip −6 → 42 KB; insights
28 %, scorecard 19 %, highlights 8 %. The API has no gzip middleware;
whether PROD's front end compresses is unverified (public endpoints
return 32-byte bodies, `server: uvicorn`).

Local-only factor: the client reaches the API through
`localhost.previewport.net`, which adds 0.3–0.5 s per request. PROD
first paint has not been traced.

## Default period "this month" (client decision, 2026-10-02) — measured, not built

Client: "Default filter view to this month." PROD replica, September
2026 as a full month, `called_at` desc. SQL time = `EXPLAIN ANALYZE`
wall minus the ≈ 180 ms VPN round trip (a count's wall vs its actual).

| default view | all time (branch) | one month (branch) | one month (master) |
|---|---|---|---|
| appointments: count | 484 ms | 57 ms | 105 ms |
| appointments: page, 200 rows | ≈ 360 ms | ≈ 80 ms | ≈ 75 ms |
| pitches: count | 1,470 ms | 192 ms | 379 ms |
| pitches: page, 200 rows | ≈ 850 ms (50 rows) | ≈ 190 ms | ≈ 210 ms |

So the bounded default cuts the default view's SQL about 6× (appointments
≈ 0.84 s → ≈ 0.14 s; pitches ≈ 2.3 s → ≈ 0.38 s). After it, running
count and page concurrently saves at most the smaller of the two
(57 ms / 190 ms) on the default view; it only pays on wide ranges.
What is left of first paint is then outside SQL: the preferences round
trip, transfer, and ≈ 0.5 s rendering at 200 rows/page.

Decided with Tomas 2026-10-02: no client-side caching (data or
preferences), no API contract change, no skeleton, compression not a
priority, no staged render (the ≈ 0.5 s at 200 rows/page stays).

### Built 2026-10-02 (local, unmerged)

- **udab-client** (`hub-performance`, off `upstream/master`):
  `DEFAULT_PERIOD = 'this_month'`; `defaultFilters(today)` carries the
  resolved dates; `sanitizeViewPreferences` sends "no period and no
  dates" to the default and keeps an explicit `all_time`. 13 tests
  updated to the new default, 2 assertions added; full suite 1,611
  passed.
- **udab-server** (`hub-performance`): `list_calls(count_db=)` gathers
  count and page on two reader sessions; route asks for the second one
  with `use_cache=False`. 1 new test (second session ≡ single session);
  86 passed.
- Local API, best of 3, 50 rows: appointments all time 1.17 s → 0.58 s,
  all kinds 1.20 s → 0.61 s, pitches 0.78 s → 0.38 s (sequential →
  concurrent). One month: 0.11 s at 50 rows, 0.41 s at 200.
- Browser check on the new client code with the local saved view
  (explicit "Last month", 25 rows): rows at 0.95 s, list request
  carries the month bounds. The default itself is covered by the unit
  tests; the local saved view was not altered to exercise it.

Saved views on PROD (checked 2026-10-02 afternoon; four rows, used by
the CEO, CTO and COO): two already store `period: "this_month"`, set by
hand that day; two are the pre-preset shape (no `period`, no dates),
which `sanitizeViewPreferences` resolves to a hard-coded `all_time`.
Changing `defaultFilters()` alone would not move those two. Options, no
DB migration needed for any: leave them and ask the users; or make the
"no period and no dates" branch fall back to the default period (hits
exactly the pre-preset rows; an explicit All time is stored as
`period: "all_time"` and stays respected). **Decided 2026-10-02: the
fallback** ("if the preferences don't state all time explicitly, This
month").

## What a page load runs

`GET /view-preferences/appointment-calls`, then in parallel
`GET /appointment-calls` (count query + page query + 6 per-page
lookups) and `GET /appointment-calls/filters` (5 DISTINCT queries, run
sequentially in one request). Building thumbnails fetch lazily as rows
scroll into view. All of it goes through `get_async_db`, i.e. the
**writer** engine (pool 10 + 20 overflow), not the reader session other
read-only routes use.

PROD population (2026-10-01): 175,683 rows all kinds, 37,071
appointments, 138,612 pitches; 994 Active Pipeline Client accounts;
`sf_task` 19.2M rows, 13.2 GB data, 5.2 GB indexes; 9.3M tasks since
2026-01-01 (population is 1.9 % of them).

## Measured today (PROD reader, warm)

| query | server | wall | notes |
|---|---|---|---|
| page, appointments, called_at desc (default) | 0.29 s | 1.2 s | sort on slim `sf_task` rows, LIMIT stops early, 7 subqueries × 50 rows |
| page, pitches, default | 0.94 s | 1.7 s | 260k-entry index range + filesort of 139k rows |
| page, all kinds, default | 1.23 s | 2.0 s | same, 176k rows |
| count, appointments | 0.78 s | 0.87 s | |
| count, pitches | 2.76 s | 2.7 s | |
| count, all kinds | 3.50 s | 3.4 s | |
| page, appointments, sort account_name | 8.2 s | 8.2 s | 29 s cold |
| page, appointments, sort transcript_state | 8.5 s | 8.5 s | |
| page, pitches, sort owner_name, 200/page (a saved preference on PROD) | 25.0 s | 25.5 s | **56.7 s cold** |
| `/filters`: accounts, owners, account owners, industries, teams | 3.7–4.1 s **each** | ≈ 19 s total | sequential in one request |

Why:

- **Non-date sorts evaluate everything before sorting.** The plan is
  `Sort → Stream results → joins`, and every projection subquery shows
  `loops=37071`: seven correlated subqueries (newest email, adherence,
  two scorecards, two next-call lookups) plus six joins are computed for
  the whole population, then sorted, then 50 kept. The default
  `called_at` sort escapes this only because MySQL can sort `sf_task`
  alone first.
- **Count and `/filters` carry joins they don't use.**
  `_population_count_select()` outer-joins owner, account owner,
  contact and lead; MySQL never eliminates outer joins, so each pass
  pays four index lookups per population row (≈ 1.5 s of the 3.5 s
  all-kinds count). Each of the five `/filters` queries repeats the
  full 176k-row pass with all joins.
- **Every full-population pass costs ≈ 2 s at best with today's
  indexes**: 260k index entries on `ix_sf_task_disposition_created`
  (42 disposition ranges), a row read per entry for `IsDeleted` /
  `TaskSubtype` / `AccountId`, then an `sf_account` lookup per row.
  There is no index on `sf_task.AccountId`, so the join cannot start
  from the 994 accounts.
- The 7 subqueries are cheap per row (next-call ≈ 0.05–0.08 ms,
  scorecards ≈ 0.02 ms); they only hurt when multiplied by the
  population, i.e. under non-date sorts.

## Rewrites, timed on PROD (no schema change)

| change | before → after (server) |
|---|---|
| count without the four unused outer joins | all 3.50 → 1.89 s; pitches 2.76 → 1.47 s; appointments 0.78 → 0.44 s |
| `/filters` as one pass: `SELECT AccountId, OwnerId … GROUP BY` over the task-side population, then join `sf_account` (filtered) and `sf_user` on the ≤ 21k distinct pairs | 5 × 3.9 s → **1.1–1.2 s for all five lists** |
| keyset page: inner `SELECT sf_task.id … ORDER BY … LIMIT` with only the joins the sort needs, outer select joins the 50 ids and runs the subqueries | account_name 8.2 → 0.47 s; transcript_state 8.5 → 1.25 s; owner_name 200/page 25.0 → 2.1 s |
| keyset on the default sort | pitches 0.94 → 0.85 s; all 1.23 → 1.11 s (not worth it alone) |
| `FORCE INDEX (ix_sf_task_created_date)` on the default page | 0.29 → **21.5 s** — dead end |
| `/*+ NO_INDEX(sf_account sf_id) */` to coax a hash join | no plan change |

The keyset shape also makes `transcript_state`'s four job-task
subqueries run on the slim inner query (where they stay cheap) rather
than alongside the other seven.

## Index candidate, A/B'd locally

`ix_sf_task_population (CallDisposition, CreatedDate, TaskSubtype,
IsDeleted, AccountId, OwnerId)` makes every population pass
index-only on the `sf_task` side. Local volume seed (458k tasks,
82,645 population rows), both indexes forced on the same statement:

| query | existing index | covering index |
|---|---|---|
| count, all kinds, no outer joins | 868 ms | 330 ms (plan: `Covering index range scan`) |
| `/filters` one-pass pairs | 649 ms | 136 ms (covering) |
| page, appointments, default | 511 ms | 488 ms (still a row read per entry — the page needs other columns) |
| page, all kinds, default | 848 ms | 720 ms |

Cost: 36.7 MB locally for 458k rows ≈ 80 B/row ⇒ **≈ 1.5 GB on PROD**,
plus maintenance on every sync write that touches `CallDisposition`,
`OwnerId` or `IsDeleted`. It would roughly halve counts and the
one-pass `/filters` again; it does nothing for the sorts and little for
the page. Existing `sf_task` indexes on PROD (none covers the
population): `(CallDisposition, CreatedDate)`, `(CreatedDate)`,
`(WhoId, IsDeleted, CallDisposition)`, `(Contact__c, IsDeleted,
CallDisposition)`, `(synety__To__c, WhoId, OwnerId, CreatedDate)`,
`(Talk_Track_Session_Id__c)`, `(sf_id, CallDurationInSeconds)`, `sf_id`.
Online DDL on a 19M-row table takes minutes: run it from a local
session before the deploy (Argo times out long migrations — see
secrets.txt notes).

## Recommendation (ordered by payoff ÷ risk)

1. **Keyset the page query** for every sort (inner id select with the
   sort's joins, outer select over the ids). Removes the 8–57 s tail;
   pure service change in `_base_select` / `list_calls`.
2. **Drop the unused outer joins from the count** (keep them only when
   `teams`, `owner_ids`, `account_owner_ids` or `search` filter on
   them). −40–45 % on every count.
3. **Rebuild `/filters` as one pass** and **cache it** (population-wide,
   user-independent; a few minutes' TTL is harmless at 1,200 new
   rows/day). Takes it from 19 s to ≈ 1.2 s, and off the page load
   entirely once cached.
4. **Move the hub routes to `get_async_readonly_db`** (reader endpoint,
   pool 20 + 30). Not a latency fix; keeps these scans off the writer.
5. **Only then decide on the covering index**: ≈ 1.5 GB for another
   ≈ 2× on counts and on an uncached `/filters`. Not needed if 3 is
   cached and 1–2 land.

Not recommended: forcing the date index (21 s), hash-join hints (no
effect), an `AccountId` index on `sf_task` (untested; the join would
still have to walk every task of 994 accounts).

Open: after 1–3 the all-kinds load is ≈ 1.2 s page + 1.9 s count,
sequential in one session. Running count and page concurrently on two
reader sessions would make it max() instead of sum(); or the count
could be dropped from the first paint and fetched after the rows.
