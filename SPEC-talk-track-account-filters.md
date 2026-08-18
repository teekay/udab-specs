# Talk Track Account Filters (DRAFT)

> **Status: draft.** Written from the client's request of 2026-08-17, before their answers to the open questions at the bottom. Do not implement until those are settled.

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

- A record-type filter offering only Pipeline Client + Sapper Consulting (the Sequencer's exact option list) covers 99.5% of existing talk tracks. This all but settles open question 1 in favor of cloning the Sequencer filter.
- The 14 talk tracks on Parent/Talent accounts would vanish from a default-filtered view. Given the client's "we only use the children accounts", these are probably historical mistakes — but worth one confirmation.
- Account statuses on talk-track accounts include **"Fall Out"** (12 talk tracks), which is *not* in the client-side `ACCOUNT_STATUSES` constant (Active, Implementing, Signing, On Hold, Canceled). The status filter's option list needs either the missing value(s) added or an explicit decision that Fall Out accounts stay hidden.
- The Abstrakt premise holds in prod: 57 "Abstrakt …" test accounts sit under Pipeline Client, so the record-type filter keeps them visible with no name-based special-casing.

## Current state (why the noise appears)

None of the talk-track surfaces filter by record type today:

- Account typeahead in the editor: `TalkTrackForm.vue` → `GET /sf-accounts?q=` → `search_sf_account_typeahead` (`app/services/opensearch_search.py`) — excludes only deleted accounts.
- Main list `GET /talk-track/search`: `account` param is a plain `ilike` on `SfAccount.Name` (`app/services/talk_track/talk_track.py` ~line 329).
- Reports `GET /talk-track/reports/accounts-missing-talk-track`: `app/services/talk_track/reports.py` filters status (Active/Implementing) + service lines only.
- Analytics `GET /talk-track/sessions`: no account-side filters.

## Proposed scope

1. **Frontend** — add a two-cluster filter bar (Sequencer style) to `TalkTrack.vue`, `Reports.vue`, `Analytics.vue`:
   - **Account filters**: Record Type multi-select, Account Status multi-select
   - **Talk track filters**: existing controls (name, draft/published/archived status, templates) — relabeled so the two "status" filters can't be confused
2. **Backend** — accept `status[]` / `recordType[]` on `/talk-track/search`, `/talk-track/sessions`, and the missing-talk-track report; filter on the joined `SfAccount`, same pattern as `strategy.py`.
3. **Filter carry-through** — selections on the main page follow the user to Reports/Analytics and stay editable on each page. Needs cross-page state (query params or a store); the Sequencer's provide/inject is per-page and doesn't carry.
4. **Parent accounts** — excluded "for now" by simply not offering Parent as a record-type option (the Sequencer precedent: only Pipeline Client + Sapper Consulting are offered).
5. **No name-based "Abstrakt" exclusion** — dropped by the client's own revision.

## Assumptions

- 'Parent', 'Inbound', 'Talent' are Account **record types** (not statuses or tags), mapped as in the table above.
- "See sequencer" means the filter bar layout and its two-record-type option list, not sequencer batch logic.
- The record-type option list itself is the exclusion mechanism — no separate hard-coded exclusion layer.
- "Account status" = `SfAccount.Status__c`, distinct from talk-track status (draft/published/archived).
- The editor's account typeahead is out of the revised scope (named in the original ask, absent from the revised page list) — see question 3.

## Open questions for the client

1. **Record type options**: offer exactly Pipeline Client + Sapper Consulting like the Sequencer (implicitly excluding Parent/Inbound/Talent)? Prod data says these two cover 99.5% of existing talk tracks, so this is now a recommendation, not an open choice — confirm, and confirm that the 14 existing talk tracks on Parent/Talent accounts (10 + 4) may disappear from the default view. Is "Talent" the record type on the "- Staffing" accounts?
2. **Default status selection**: Active only (Sequencer default), or Active + Implementing (what Reports targets today)? Also: prod has account statuses outside the `ACCOUNT_STATUSES` constant (e.g. "Fall Out", carried by 12 talk tracks) — should the option list include them?
3. **Editor account search**: should the create-talk-track typeahead be restricted to the same record types? It currently returns every non-deleted account, Parent included.
4. **Reports interaction**: the missing-talk-track report hard-codes status Active/Implementing and specific service lines. Does the new Account Status filter override that status list, or does the report keep its fixed population and gain only the record-type filter?
5. **Account tags**: the Sequencer's Account cluster also has Tags. In scope here or not?
6. **Carry-through mechanics**: should filters survive refresh / be shareable via URL (query params), or is in-session memory enough?
7. **Analytics detail**: account filters apply to the session's account, correct?

Question 1 decides the shape of the work: either clone the Sequencer's two-option filter as-is, or introduce record-type names the codebase doesn't currently have (there is no record-type name table locally — only the two hard-coded ID constants).
