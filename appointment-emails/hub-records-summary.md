---
kind: spec
status: draft
area: appointment-emails
updated: 2026-10-13
repos: [udab-server, udab-client]
summary: "Procedural sentence above the Hub table (count, excellent, DM, needs review, avg stars) from one aggregate query."
---

# Account Management Hub — summary line above the records

Client (2026-10-13) wants a sentence above the Hub table that sums up the
rows currently shown. Their example:

> 6 appointments in view. 2 excellent, 3 with a confirmed decision maker
> or influencer. 2 need review before the client sees them. Average 3.4
> stars.

The sentence is **procedural**: a template over five numbers that come
from one aggregate query. It is recomputed on every load and filter
change, so it cannot involve a model call. Every number maps to a column
the Hub already loads per row; only the word "excellent" has no
definition yet (open question below).

## Open questions

### Client-facing

- [ ] **Q1. What is "excellent"?** Asked 2026-10-13, no answer yet. The
  original Hub ask (call-queue.md) already deferred the "plain-language
  grade of the opportunity". Candidates from the data we have, so the
  answer can be mapped to a rule:
  - Pipeline card stars ≥ 4 (the Opportunity Grade column). Only exists
    once the reviewer has scored the card after the meeting.
  - Opportunity heat ≥ 4 (0–5 "meat on the bone"). Pitch calls only.
  - A combination with the insight's `urgency` / `agreement_to_meet`.
  *(lean)* stars ≥ 4 on Appointments, heat ≥ 4 on Pitches; wording and
  threshold are the client's call.
- [ ] **Q2. Which signals make a call "need review before the client sees
  it"?** Proposed: any insight `flags` item (the AM-only warnings column),
  or `wrong_contact`, or `do_not_call_requested`. `speaker_labels_suspect`
  and `validation_flags` are data-quality signals about the extraction,
  not about the prospect; *(lean)* leave them out. The client's example
  counts 2 of 6, so the rule should not be so wide that most rows match.

### Internal

- [ ] **Q3. "In view" = the whole filtered set, not the current page.**
  *(lean)* whole set. Page-only numbers change with the page-size
  selector and the page number, which would read as the data moving.
  The cost is one more aggregate query of the count query's shape
  (timings below).
- [ ] **Q4. Where the stars average comes from.** The Opportunity Grade
  column is the newest Pipeline card for the contact with `Appt_Date__c`
  inside `SCORECARD_WINDOW_DAYS` after the call and `Stars_Count__c > 0`.
  That is a correlated subquery per task; over the whole filtered set it
  is the only part of the summary that is not a plain column. Measure on
  PROD before deciding whether it needs a bound (see Performance).

## Definitions (procedural, per row)

| Clause | Rule | Source |
|---|---|---|
| N in view | the list's existing `total` | count query |
| excellent | **Q1** | `Stars_Count__c` (Pipeline card) or `sp_call_insight.opportunity_heat` |
| confirmed decision maker or influencer | `decision_maker IN ('decision_maker', 'influencer')` | `sp_call_insight.decision_maker` (null / `unconfirmed` otherwise) |
| need review | **Q2**; proposed: has a `flag` item, or `wrong_contact`, or `do_not_call_requested` | `sp_call_insight` + `sp_call_insight_item.kind = 'flag'` |
| average stars | `AVG(Stars_Count__c * 5)` over rows that have a scored Pipeline card; stored as a fraction of five (0.2 per star, see `_grade_payload`) | same subquery as `opportunity_grade` |
| scored | count of rows that enter the average | same |

Rows without a completed insight count as "not confirmed" and "no
review needed", never as unknown: the sentence states facts about what
the data says today, the per-cell reasons (hub-empty-cells.md) explain
the gaps.

**Stars are mostly empty.** Area notes, 2026-10: 618 of 934 appointment
calls this month have a Pipeline card and 0 have stars, because cards
are scored after the meeting. The average must therefore say how many it
covers ("Average 3.4 stars across 12 scored") and drop out entirely when
nothing is scored. A bare "Average 0 stars" would be wrong.

## Sentence template

Five numbers (`total`, `excellent`, `decision_maker`, `needs_review`,
`scored`) and one float (`avg_stars`), rendered on the client:

```
{total} {appointments|pitches|calls} in view.
[{excellent} excellent, ]{decision_maker} with a confirmed decision maker or influencer.
[{needs_review} need[s] review before the client sees {it|them}.]
[Average {avg_stars:.1f} stars across {scored} scored.]
```

- The noun follows the view (Appointments / Pitches / both kinds).
- Zero rows: "No appointments match the current filters." and nothing
  else (the table already shows the same message; the summary line
  stays so the layout does not jump).
- Until Q1 is answered the "excellent" clause is omitted, not rendered
  as 0. Ship without it if the client is slow; add it as a one-line
  change.
- `needs_review` of 0 renders "None need review." rather than dropping
  the clause, since its absence is the reassuring fact.
- Singular/plural: "1 appointment", "1 needs review", "1 scored".

## Server

`GET /appointment-calls` response gains `summary`:

```json
"summary": {
  "total": 6,
  "excellent": 2,
  "decision_maker": 3,
  "needs_review": 2,
  "scored": 4,
  "avg_stars": 3.4
}
```

`total` duplicates the top-level `total` so the object stands alone;
`excellent` is `null` until Q1 is answered (absent clause on the
client). `avg_stars` is `null` when `scored` is 0.

Implementation in `app/services/appointment_calls.py`:

- Replace the `count_stmt` in `list_calls` with one aggregate over the
  same `_population_select(..., filters.joins() | {JOIN_INSIGHT})`
  subquery: `COUNT(*)`, `SUM(CASE decision_maker IN (...) )`,
  `SUM(CASE needs_review)`, `COUNT(stars)`, `AVG(stars)`, where `stars`
  is `_scorecard_subquery(SfQualityScorecard.Stars_Count__c,
  SCORECARD_TYPE_PIPELINE, Stars_Count__c > 0)` selected in the inner
  population select (the `grade` sort already builds exactly this
  expression, see `sort_expressions`). The `flag` item test is an
  `EXISTS` on `sp_call_insight_item` (kind = flag) correlated on the
  insight.
- The count already runs on a second reader session concurrently with
  the page query (`count_db`); the aggregate takes its place, so the
  request shape does not change.
- `JOIN_INSIGHT` is an outer join, one row per task (hub-sorting.md), so
  it does not change the count.
- The sum/avg SQL lives in one function, `summary_expressions()`, next
  to `sort_expressions`, so Q1's rule is one line when it lands.

### Performance

Baseline for the count query (hub-performance.md, PROD, warm):
appointments ≈ 57–105 ms, pitches ≈ 192–379 ms. The aggregate adds the
insight join (already measured for the insight sorts) and the Pipeline
stars subquery per row of the filtered set. The scorecard sorts walk the
same subquery per task and were A/B'd with
`ix_sf_quality_scorecard_contact_type_date` in hub-sorting.md; expect
the aggregate to sit between the count and a scorecard-sorted page.
Measure on PROD with the widest default filter (14 days, all accounts)
and on an all-time range before merging. If the all-time range is slow,
the fallback is to compute the stars part only when the filtered total
is under a cap (say 5,000) and render "Average stars not shown for
ranges this wide" otherwise. Do not cache: the filters are the cache key
and the data changes every minute (poller, insights sweeper).

### Tests

`tests/test_appointment_calls.py`: summary over a seeded set with known
insight values and scorecards; each clause's rule; `scored = 0` gives
`avg_stars = null`; filters narrow the summary with the list; Pitches
view counts heat when Q1 says so.

## Client

`AppointmentCallsPage.vue`: a `summary` ref filled from the list
response, rendered as one `<p>` between the toolbar and the table,
muted, same row as the existing "**N** calls" counter (replace that
counter: the sentence starts with the same number). The template logic
(pluralisation, optional clauses, zero case) goes in a pure function in
`src/constants/appointment-calls.js` or a small `hubSummarySentence()`
module with a vitest file; the component only calls it.

**Client-safe view: out of scope.** The switch is still live on
master. The commit that commented it out (udab-client `10ddcf9`,
2026-10-06) sits on the unpushed local branch
`itemize-null-column-reasons` and never reached a PR; the client is
still deciding what the view should hide (2026-10-13, corrected
2026-10-20). When it comes back, the "need review"
clause is AM-only and must drop out of the sentence the same way the
flags column does (`CLIENT_SAFE_HIDDEN_KEYS`); note it there, do not
build it here.

## Not in scope

- Any model-generated prose. If the client later wants a narrative
  summary, it is a separate feature with its own cost and cadence
  (one generation per filter combination is not viable).
- Per-account or per-rep breakdowns inside the sentence.
- The summary in the ZIP export.
