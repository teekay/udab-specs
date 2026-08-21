# Talk Track Account Filters (DRAFT)

> **Status: work in progress.** Written from the client's request of 2026-08-17; updated 2026-08-18 with prod-data findings, the client's round-2 answers, and the iteration-1 course correction below. Everything below "Course correction" is background for later iterations — several questions there remain open.

## Course correction — iteration 1 (2026-08-18, implemented)

The real need behind the request turned out to be simpler than the sequencer-style filter bar: people land on the Talk Track page thinking "show me all NVIDIA talk tracks", and the free-text Account filter (substring match on account name) serves that poorly. The sequencer comparison was an implementation hint, not the requirement.

**Iteration 1** (done, pending review): replace the free-text Account filter on the Talk Track main page with the same async account autocomplete used in the create-talk-track form, filtering by exact account match.

- `udab-client/src/pages/talk-track/TalkTrack.vue` — free-text input replaced with `@vueform/multiselect` (async options from `GET /sf-accounts?q=`, same UX as `TalkTrackForm.vue`); sends `account_id` instead of `account` to `/talk-track/search`; dark-theme multiselect overrides copied from the form (they were scoped to it by mounting).
- `udab-server/app/routes/talk_track.py` — `/talk-track/search` gains an `account_id` param (exact match, takes precedence over `account`). The service layer already supported it; the `account` name-substring param remains for compatibility.
- `udab-server/tests/test_talk_track_routes.py` — new `test_search_by_account_id`.

**Refinement (same day):** `GET /sf-accounts` restricts results to five account statuses by default, which made talk tracks on accounts in other statuses (e.g. Prospect, Fall Out) unreachable through the filter. Decision: the **list-page filter searches accounts in any status** (new `all_statuses=true` param on `/sf-accounts`, passed by `TalkTrack.vue`), while the **create-form picker keeps the status restriction** — creating talk tracks targets current clients, but filtering must reach everything. Default endpoint behavior is unchanged.

Next iterations (account status / record type filters, roll-up) will be driven by user feedback; the analysis below stays relevant for them.

## Problem

Talk-track surfaces (main list, Reports, Analytics, and the account picker when creating a talk track) show accounts of every Salesforce record type — including Parent, Inbound, and Talent accounts that the team never attaches talk tracks to. The client originally asked to hard-exclude those record types plus any account whose name contains "Abstrakt", then revised the ask: no hard-coded exclusions — instead, add the same Account Status + Account Record Type filters that the Sequencer page already has, with defaults that hide the noise.

The revision matters because the ~55 "Abstrakt …" test accounts (used for the bulk of their testing) sit under the **Pipeline Client** record type. A record-type filter defaulting to Pipeline Client + Sapper Consulting hides Parent/Inbound/Talent accounts while keeping the Abstrakt test accounts visible; a `name LIKE '%Abstrakt%'` exclusion would have hidden the test accounts too.

## The Sequencer reference

"See sequencer for examples on filters/layout" refers to `udab-client/src/pages/sequencer/components/SequencerFilterBar.vue`, rendered by `Sequencer.vue`. It groups filters into labeled clusters separated by a vertical divider:

- **Account** cluster: Status, Record Type, Tags — all `CheckboxMultiSelect` (`src/components/form/CheckboxMultiSelect.vue`)
- **Strategy** cluster: strategy-specific filters

That is the model for the requested split into **Account filters** vs **Talk track filters**.

Existing building blocks:

| Piece | Where |
|---|---|
| `ACCOUNT_STATUSES` (Active, Implementing, Signing, On Hold, Canceled) | `udab-client/src/constants/constants.js` |
| `RECORD_TYPES` — Pipeline Client `012A0000000kZfwIAE`, Sapper Consulting `0124w000001YqDwAAK` | same file |
| Backend filter pattern: `status[]` / `recordType[]` query params → `SfAccount.Status__c` / `SfAccount.RecordTypeId` | `udab-server/app/routes/strategy.py` (`_build_datatable_base_query`, ~line 97) |
| Sequencer defaults | status = `[Active]`, record type = none selected (= all offered) |

## Record type mapping (verified against prod reader, 2026-08-18)

uDab's code names only two record types (the ID constants above). The others exist only as raw `RecordTypeId` values. Verified against the prod read replica (`sapper` DB) via name-pattern analysis:

| RecordTypeId | Accounts (prod) | Evidence | Label |
|---|---|---|---|
| `012A0000000kZfwIAE` | 73,612 | — | **Pipeline Client** (named in code) |
| `0124A000001BTyZQAW` | 4,375 | 3,864 names end "- Parent" | **Parent** |
| `0124w000001YqDwAAK` | 1,763 | — | **Sapper Consulting** (named in code) |
| `012A0000000kZbFIAU` | 1,372 | 748 names contain "Inbound" (also "- Social Boost") | **Inbound** |
| `0124A000001Qc5kQAC` | 1,213 | internal-looking names ("Life at Abstrakt", "Cafe Piazza Orders") | internal/house accounts |
| `0124w000001NbCpAAK` | 744 | 496 names contain "Staffing"/"Talent" | **Talent** (Abstrakt Talent Solutions = staffing arm) |
| ~10 more types | < 250 each | auto dealers, Creative, Cloud Solutions, … | long tail |

Official Salesforce `RecordType.Name` values could not be pulled via SOQL (dev sandbox credentials are stale — `invalid_grant`), but the name-pattern evidence above is decisive for Parent/Inbound and strong for Talent.

## Prod findings on actual talk-track usage (2026-08-18)

Queried the prod read replica: which accounts do the 2,867 existing talk tracks attach to?

| Account record type | Talk tracks | Share |
|---|---|---|
| Pipeline Client | 2,838 | ~99% |
| Sapper Consulting | 14 | 0.5% |
| Parent | 10 | 0.3% |
| Talent | 4 | 0.1% |
| Inbound | 0 | — |

Consequences:

- Pipeline Client + Sapper Consulting alone cover 99.5% of existing talk tracks; the client's round-2 picklist adds Talent Solutions Account and AgencyClient on top (4 and 0 existing talk tracks respectively, assuming the presumed ID mapping).
- The 10 talk tracks on Parent accounts become permanently invisible under the round-2 hard-exclusion. Given the client's "we only use the children accounts", these are probably historical mistakes — but flag it (question 3).
- **Account status inventory (prod)**: 12 distinct `Status__c` values exist — Prospect (66,091), Canceled (11,555), Active (2,741), Signing (1,962), Fall Out (558), On Hold (247), Implementing (183), Website Hosting Only (73), Onboarding (60), Paused (18), Pending Cancellation (8), NULL (4). The client-side `ACCOUNT_STATUSES` constant covers only five of these.
- **Statuses of accounts that actually hold talk tracks** (6 of the 12): Active (884 accounts / 1,797 talk tracks), Canceled (520 / 991), On Hold (20 / 40), Implementing (21 / 22), Fall Out (9 / 12), Signing (4 / 4). Two things stand out: no Prospect account holds a talk track (so the dominant status is irrelevant to this UI), and roughly a third of all talk tracks — 991, including 441 published — sit on **Canceled** accounts. A default of Active(+Implementing) hides those; the filter being editable makes them reachable, but the client should know the default hides a third of the data.
- The status filter's option list should be the six statuses above (or all twelve) — the current five-value `ACCOUNT_STATUSES` constant is missing Fall Out at minimum.
- The Abstrakt premise holds in prod: 57 "Abstrakt …" test accounts sit under Pipeline Client, so the record-type filter keeps them visible with no name-based special-casing.

## Current state (why the noise appears)

None of the talk-track surfaces filter by record type today:

- Account typeahead in the editor: `TalkTrackForm.vue` → `GET /sf-accounts?q=` → `search_sf_account_typeahead` (`app/services/opensearch_search.py`) — excludes only deleted accounts.
- Main list `GET /talk-track/search`: `account` param is a plain `ilike` on `SfAccount.Name` (`app/services/talk_track/talk_track.py` ~line 329).
- Reports `GET /talk-track/reports/accounts-missing-talk-track`: `app/services/talk_track/reports.py` filters status (Active/Implementing) + service lines only.
- Analytics `GET /talk-track/sessions`: no account-side filters.

## Client answers — round 2 (2026-08-18)

Verbatim decisions from the client's follow-up:

- **Record type picklist has four options**: Pipeline Client, Sapper Consulting, Talent Solutions Account, AgencyClient. (Talent thus moved from "hide" in the original ask to "selectable".)
- **Parent Account is hard-excluded** — filtered out entirely, not offered in the picklist. Talk-track surfaces never show Parent accounts regardless of filter state. The 10 existing talk tracks on Parent accounts become unreachable through these views.
- **Sequencer mimicry clarified**: clone the sequencer's account **status** filter verbatim; clone the record type filter as a component but **swap its picklist** for the four options above.
- **New ask, meaning unclear**: "could we have the talk tracks roll up to the account? Then we could still filter by account filters AND/OR talk track filters but have everything at least rolling up." See open question 2 — this is a scope fork, not a detail.

The client referenced a linked doc ("info regarding types linked above") for the record types; that link is needed to map the two new picklist names to RecordTypeIds (open question 1).

## Proposed scope

1. **Frontend** — add a two-cluster filter bar (Sequencer style) to `TalkTrack.vue`, `Reports.vue`, `Analytics.vue`:
   - **Account filters**: Record Type multi-select (four options: Pipeline Client, Sapper Consulting, Talent Solutions Account, AgencyClient), Account Status multi-select (cloned from the sequencer)
   - **Talk track filters**: existing controls (name, draft/published/archived status, templates) — relabeled so the two "status" filters can't be confused
2. **Backend** — accept `status[]` / `recordType[]` on `/talk-track/search`, `/talk-track/sessions`, and the missing-talk-track report; filter on the joined `SfAccount`, same pattern as `strategy.py`. Regardless of filter params, restrict results to the four picklist record types (this is what implements the Parent hard-exclusion).
3. **Filter carry-through** — selections on the main page follow the user to Reports/Analytics and stay editable on each page. Needs cross-page state (query params or a store); the Sequencer's provide/inject is per-page and doesn't carry.
4. **No name-based "Abstrakt" exclusion** — dropped by the client's own revision.
5. **Account roll-up** — shape TBD pending open question 2; likely an account-grouped presentation of the talk-track list and/or per-account aggregation in Analytics.

## Assumptions

- The four picklist names are Account **record types**; "Talent Solutions Account" = the "- Staffing" type `0124w000001NbCpAAK` (strong name-pattern evidence). "AgencyClient" is unmapped — see question 1.
- "Account status" = `SfAccount.Status__c`, distinct from talk-track status (draft/published/archived).
- The backend enforces the four-type restriction unconditionally (Parent exclusion is not a client-side default that can be toggled off).
- Account Tags are out of scope: the client said to mimic status and swap the record-type picklist, and never mentioned the sequencer's third account filter.
- The editor's account typeahead is out of the revised scope (named in the original ask, absent from the revised page list) — see question 4.

## Settled by client round 2

- Record type picklist: the four options listed above; no Inbound option.
- Parent Account: always excluded, no picklist entry.
- Status filter: clone the sequencer's (options and behavior).

## Open questions for the client

1. **RecordTypeId mapping** (blocker): which IDs are "Talent Solutions Account" and "AgencyClient"? The client's linked record-types doc should answer this. Presumed: Talent Solutions Account = `0124w000001NbCpAAK` ("- Staffing" accounts). AgencyClient candidates: `0124A000001Qc5kQAC` (1,213 accounts, plain company names) or `012A0000000kZbFIAU` (1,372 accounts, "- Inbound SDR"/"- Social Boost"). The original request excluded 'Inbound', which argues AgencyClient is the former — but this must be confirmed, not guessed. (No record-type name table exists locally; SF sandbox creds are stale.)
2. **"Roll up to the account"** (scope fork): which shape is meant?
   - a) Main list grouped by account — account rows expand to show their talk tracks
   - b) Analytics aggregated per account — sessions/usage totaled at account level
   - c) A new account-level view — one row per account with talk tracks, status, usage
   Also: does "roll up" ever mean aggregating child-account talk tracks up to the Parent account? That would sit oddly with the Parent hard-exclusion, so worth an explicit yes/no.
3. **Status defaults & data caveats**: Active-only default (sequencer's) hides the ~1,000 talk tracks on Canceled accounts (441 published); the sequencer's five-status list has no "Fall Out", leaving those accounts (12 talk tracks) unreachable. Confirm both are acceptable. Same for the 10 talk tracks on Parent accounts, which the hard-exclusion makes permanently invisible.
4. **Editor account search**: should the create-talk-track typeahead be restricted to the same four record types? It currently returns every non-deleted account, Parent included.
5. **Reports interaction**: the missing-talk-track report hard-codes status Active/Implementing and specific service lines. Does the new Account Status filter override that status list, or does the report keep its fixed population and gain only the record-type filter?
6. **Carry-through mechanics**: should filters survive refresh / be shareable via URL (query params), or is in-session memory enough?
7. **Analytics detail**: account filters apply to the session's account, correct?
