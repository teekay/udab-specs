# ZoomInfo Exit — Prospect Archive & Salesforce Contact Deletion

**Status: in progress — Slices 0–1 unblocked by the 2026-07-14 decisions below; the destructive slice (2) stays blocked until the client ratifies the remaining questions and signs off the dry-run report.**

## Decisions (2026-07-14, Tomas)

1. **Scope cut**: Salesforce Leads and Smartlead campaign removal are **deferred** (open questions C11/C12 stay open but don't block).
2. **5x5 match** = case-insensitive full-string equality between the prospect's email(s) and **two** columns on `5x5_universal_person`: `business_email` and `personal_emails` (decision revised 2026-07-14 after the 400M-row constraint surfaced; the multi-email Text columns `additional_personal_emails` / `programmatic_business_emails` are **not** used). Any hit = defense = keep. `5x5_universal_person_archive` is empty on prod → out of the picture (A2 resolved).
3. **Default posture is KEEP**, for both actions. Archive a prospect only when it is provably ZoomInfo-sourced AND no defense exists. Delete a contact only when it is provably ZoomInfo-only AND meets the client's delete criteria AND no keep rule matches. Anything ambiguous → keep, and surface it in the dry-run report. (This deliberately reverses the reviewed code's default-delete posture — flagged to client in B8.)
4. **Dry-run is a first-class deliverable**: every evaluated record gets a decision + machine-readable reason codes (which defense fired, or why it's a candidate), exportable for client review.
5. **Full audit log** of destructive actions: durable record (DB table + S3 export) of what was archived/deleted, when, by which job, with a snapshot of the deleted contact.

## Overview

Abstrakt has discontinued its relationship with ZoomInfo and may no longer use data that traces back *only* to ZoomInfo as a source. Two deliverables:

1. **`sp_prospect` archive**: archive (not delete) every prospect that only has a ZoomInfo identity — no 5x5 Universal Person match and no protected Salesforce dev phase/outcome association. Set `archived = 1`, `archive_reason = 'zoominfo exit'`, `archived_at = <run date>`.
2. **`sf_contact` deletion**: delete from Salesforce (and handle the local mirror) contacts that only trace back to ZoomInfo and meet the deletion criteria, **unless** they match any rule in the client's keep document (reproduced below).

Additionally, the client asked us to **remove the pre-tagging approach** built so far: the staged "unmatched" tables and the commands that populate them. Pre-tagging produces a point-in-time snapshot that goes stale; the replacement is a single typer command that evaluates each record in realtime and archives/deletes in the same pass.

```
per sp_prospect row (id range shard):
  ZoomInfo-sourced? ──no──► skip
  5x5 UP match?     ──yes─► keep
  SF match with protected phase/outcome? ──yes─► keep
  else ► archive prospect ('zoominfo exit')
         └─ matched sf_contact ZoomInfo-only + deletable + no keep rule? ► delete in SF
```

---

## Current State (Marshall's pipeline)

All code lives in `app/commands/five_five/` (registered in `app/commands/__init__.py`). It is a **multi-stage staging pipeline; nothing destructive is currently runnable** — the final archive/delete commands are review-only stubs that count and log.

### Provenance — where the work lives (verified 2026-07-13)

- **Everything is merged.** For all archive-related files, `master` == `prod` == `stage` on upstream (`SapperConsulting/udab-server`, a.k.a. `abstrakt-mg`). The `5x5_unmatched` branch is fully merged into master (0 unmerged commits). No other upstream branch carries unmerged archive work (checked all 48 branches by content diff).
- Marshall worked from a personal fork branch `marshall-johnson/fix/prospUp`; his last commit (`a994c50`) merged via PR #617 on **2026-06-03**. The fork is no longer accessible (deleted or private), there are no open PRs from him, so **there is no unmerged exclusion-list work sitting anywhere** — the client's "might be unmerged in a branch" is moot; it's all in master/prod.
- The **exclusion (keep) list is implemented and merged**: all 14 contact keep rules plus the Leads keep list live in `find_archive_contact.py`, built in the May 19–26 rewrite (commits `47834d9` → `e5ed24c` chain → `edee7f3`). This is the artifact the client reviewed and confirmed correct.

### History of the destructive step — implemented once, then deliberately defused

- **Feb 13 (`21d9447`)**: initial version. Staging rule was crude (`DevPhase__c IS NULL AND DevOutcome__c IS NULL` — no keep document yet). `delete_archived_contact` **was fully destructive**: local `sf_contact` soft-delete → `sp_prospect` archive (`archived_reason='Zoominfo Exit'`) → Salesforce `delete_object("Contact", …)` per record → Smartlead lead deletion via API.
- That version had a **critical bug**: it passed `ContactUnmatched.id` staging PKs (local autoincrement ints) both to the `SfContact.Id` filter and to the Salesforce delete call, instead of Salesforce IDs. It also predated the keep document entirely.
- **May 19 (`47834d9`)**: same commit that began implementing the keep document also **replaced the destructive body with the review-only stub** ("Review-only mode: … No local soft-delete, sp_prospect archive, Smartlead delete, or Salesforce delete was performed").
- Net: **the exclusion list has never been wired to an actual delete.** The keep rules exist and are trusted; the delete/archive execution must be written fresh (the old destructive code is not a usable starting point).

### Code-implied answers to open questions (client reviewed this code — confirm they stand)

The reviewed implementation already embodies positions on several questions below:

- **B6 (Intro conflict)**: code keeps `01 Intro 1/2/3` but does **not** keep plain `01 Intro` → plain-Intro contacts are deletable.
- **B7/B8 (delete list)**: code does **not** gate on the "Cleansing, Suspect, Intro, No response" list at all — anything failing every keep rule is a delete candidate (`Research Dead End`, `Not a Fit`, `Out Of Business`, `Duplicate` included). I.e., the epic's delete list is descriptive, not a filter, and the effective posture is **default-delete** — the opposite of the keep document's "better to do any that aren't clearly NOT what we sourced" hedge. Needs explicit confirmation.
- Minor gap found: the HR Generic rule (`Source__c NOT IN ('zoom info','zoominfo')`) does not keep rows with **NULL** `Source__c` (SQL three-valued logic) — blank-source HR Generic contacts are only saved if another rule catches them. Confirm intent.

### Stage 1 — 5x5 match import (`find_gz_csv_unmatched.py`)

5x5 ran a match job externally and delivered gzip CSVs to S3. Each row carries an `up_id` when 5x5 matched it. For rows with **no** `up_id`, the importer (`five_five_gz_csv_file_process`) looks up `sp_prospect` by email/LinkedIn and, if the prospect has a `zoominfo_contact_id` or `zoominfo_url`, copies it into **`sp_prospect_unmatched`**.

`sp_prospect_unmatched` therefore *is* the "ZoomInfo-only" pre-tag — a point-in-time snapshot (the staleness the client objects to).

### Stage 2 — Contact candidate staging (`find_archive_contact.py` → `find_archived_contact`)

Joins `sp_prospect_unmatched` to `sf_contact` **by email equality only**, applies the keep rules from the client's document, inserts survivors into **`sp_contact_unmatched`**. The keep-rule SQL matches the document faithfully (client confirmed it was reviewed and correct) — reuse it as the reference implementation of the keep rules.

### Stage 3 — Lead candidate staging (`find_archived_lead`)

Same pattern against `sf_lead` using the Leads keep list → **`sp_lead_unmatched`**. Archive fields were also added to `sf_lead` (migration `1a9c4e7b2d5f`).

### Stage 4 — Smartlead staging (`find_archived_smartlead`)

Smartlead campaign leads tied to staged prospects → **`sp_smartlead_prospect_unmatched`**.

### Stage 5 — stubs

`archive_archived_contact`, `archive_archived_lead`, `archive_archived_smartlead`, `delete_archived_contact` are review-only. The `delete_archived_contact` log message reveals the intended full action set: "local soft-delete, sp_prospect archive, Smartlead delete, Salesforce delete".

### Findings from code review

- **Email-only association.** The prospect↔contact link is `email =` only. There is also a direct link table `sp_contact_prospect` (prospect_id ↔ SF contact_id, written when our campaigns create contacts) which the pipeline ignores. See open question A5.
- **Importer match bug** (`find_gz_csv_unmatched.py:132`): when a LinkedIn URL is present, the query `AND`s two mutually exclusive equality conditions (`linkedin_url == X` and `linkedin_url == 'https://www.' + X`), so LinkedIn-bearing rows can never match. Consequence: `sp_prospect_unmatched` is likely **under-populated**. Moot once the importer is deleted, but it disqualifies the pre-tag table as a source of truth.
- **In-memory keep IDs.** Keep rules for tasks/events/opportunities/projects are pre-loaded as giant tuples and inlined into `IN (...)` clauses (`_load_contact_keep_ids`, `find_archive_contact.py:449`). Fine for batch staging; the realtime command should use `EXISTS` subqueries instead.
- **DevOutcome normalization map exists** (`DEVOUTCOME_NORMALIZATION`, `find_archive_contact.py:84`) — raw SF data has typos ("cleansing max reache", "client reqeust"). Keep and extend it.
- **Reusable infrastructure in the same file**: AWS Batch shard-launcher pattern (`launch_sfdc_sync_contact_local_id_shards`, `find_archive_contact.py:831`) and a MySQL lock-wait retry helper (`_is_retryable_mysql_lock_error` / `_process_contact_record_with_retry`).
- `Sfdc.delete_object()` already exists (`app/services/sfdc.py:311`) — single-record REST delete, treats already-deleted as success (idempotent).

---

## Domain Model Reference

### `sp_prospect` (`app/models/prospect.py`)

| Column | Notes |
|---|---|
| `zoominfo_contact_id` | `String(50)`, nullable — ZoomInfo identity marker |
| `zoominfo_url` | `String(255)`, nullable — secondary ZoomInfo marker (importer checks either) |
| `archived` | bool, default 0 — **target field** |
| `archive_reason` | `String(255)` — set to `'zoominfo exit'` |
| `archived_at` | date — set to run date |
| `data_source_id` | FK to `sp_data_source` ("ZoomInfo, Apollo, etc.") — possibly relevant to "only traces to ZoomInfo" (question A3) |
| `email`, `linkedin_url`, phones | candidate 5x5/SF match keys |

There is **no** Universal Person FK on `sp_prospect` — "has a 5x5 UP ID" is derived, not stored (question A1).

### `5x5_universal_person` (`app/models/universal_person.py`)

Keyed by `up_id`. Candidate match fields: `business_email`, `personal_emails`, `additional_personal_emails`, `programmatic_business_emails`, `linkedin_url`, `mobile_phone`/`direct_number`/`personal_phone`. There is also `5x5_universal_person_archive` (question A2).

### `sf_contact` (`app/models/sf_contact.py`)

| Column | Notes |
|---|---|
| `ZoomInfo_Contact_Id__c` / `ZoomInfo_Company_Id__c` | indexed; alternative definition of the deletion universe (question A4) |
| `DevPhase__c` | observed values: `01 Suspect`, `02 Cleansing`, `03 Lead`, `04 Suspended` |
| `DevOutcome__c` | observed values incl. `01 Intro`, `04 No Response`, `03 No Interest`, `Appt A`, `Research Dead End`, `Not a Fit`, `Out Of Business`, `Duplicate`, `Cleansing Max Reached`… |
| `IsDeleted` | local soft-delete mirror flag |

Record type IDs (constants in `find_archive_contact.py`): Client Contact, Billing Contact, Talent Solutions, HR Generic, plus `Sfdc.GENERIC_PIPELINE_RECORD_TYPE` / `Sfdc.ABSTRAKT_PIPELINE_RECORD_TYPE`.

Keep rules also read: `sf_task` (CallDisposition ∈ appointment/contact/kdm-pitched via WhoId or Contact__c), `sf_event` (WhoId), `sf_opportunity` (Contact_ID__c), `sf_project` (name contains "pipeline referrals"/"web leads"), plus contact fields `LeadSource`, `List_Tag__c`, `Primary_List_Source__c`, `Last_Contact_Datetime__c`, `of_Contact_Calls__c`, `Source__c`, `Project__c`.

---

## Client Requirements (verbatim intent)

### sp_prospect archive

Archive records that **only** have a ZoomInfo ID and neither:

- a 5x5 Universal Person match, nor
- a Salesforce match with dev phase/outcome in: **Appt, Lead Wait, Info, TOC, Appt Disqualified, Appt Rescheduled**.

Populate `archived`, `archive_reason = 'zoominfo exit'`, `archived_at`.

### sf_contact deletion

Delete records that only have a ZoomInfo ID **and** dev phase/outcome in: **Cleansing, Suspect, Intro, No response** — but never delete anything matching the keep document below.

### Keep document (contacts must NOT be removed from Salesforce)

**Contacts**

1. Record Type = Client Contact
2. Record Type = Billing Contact
3. Record Type ∈ (Generic Pipeline, Abstrakt Pipeline, Talent Solutions) AND DevOutcome ∈ (Appt A/B/C, 01 Intro 1/2/3, Wait, Info, 03 No Interest, 07 TOC Open, Appt Rescheduled, Appt Disqualified, Appt Sold)
4. Record Type = Abstrakt Pipeline AND contact is in an Opportunity's Opp Contact field
5. Record Type ∈ (Generic Pipeline, Abstrakt Pipeline, Talent Solutions) AND has activity with call disposition ∈ (Appointment, Contact, KDM-Pitched)
6. Record Type = Abstrakt Pipeline AND listed in the Name (WhoId) field of an Event
7. Record Type = Abstrakt Pipeline AND LeadSource ∈ (Email, Web Lead, Customer Referral, LinkedIn, Self-Sourced, AMG Email, AMG 90 Day Client, Employee Referral, Upsell, Call In, SDR Email)
8. Record Type = Talent Solutions AND LeadSource ∈ (Indeed, Zip Recruiter, LinkedIn, Monster, Paylocity, Job Target, Hireology, LinkedIn-Se, Web Lead)
9. \# of Contact Calls > 0
10. Last Contact Date/Time is not blank
11. List Tag = A
12. Primary List Source not in (blank, internal salesforce, ZoomInfo, Abstrakt Database)
13. Project ∈ (Pipeline Referrals, Web Leads)
14. Record Type = HR Generic Contact AND Source ∉ (Zoom Info, ZoomInfo)

**Leads** (in scope? — question C11)

Keep if: LeadSource ∈ internal-effort list; IsConverted = true; DevOutcome ∈ keep list; Google Click ID not blank; Last Contact Date/Time not blank; Primary List Source not in exclusion list; Project ∈ (Pipeline Referrals, Web Leads).

The document twice notes "there are probably more here … better to do any that aren't clearly NOT what we sourced" → default-keep posture for ambiguous values (question B8).

---

## Target Design

### Command

```
python -m app.commands zoominfo-exit --dry-run --from-id <int> --to-id <int>
```

- Select `sp_prospect.id` in **batches of 100** (`id > from_id AND id <= to_id`, ordered by id).
- Evaluate each prospect **in realtime** (no staging tables):
  1. Skip unless ZoomInfo-sourced (`zoominfo_contact_id` or `zoominfo_url` present — pending A3) and `archived = 0`.
  2. **5x5 check** — match against `5x5_universal_person` on the agreed keys (A1). Match → keep.
  3. **SF check** — find matching `sf_contact` / `sf_lead` rows (email; possibly `sp_contact_prospect` — A5). Any match with a protected phase/outcome (B9/B10) → keep.
  4. Otherwise **archive**: single-row `UPDATE sp_prospect SET archived=1, archive_reason='zoominfo exit', archived_at=... WHERE id = :id`, flushed/committed **one record at a time** (client requirement, avoids lock contention).
  5. For matched ZoomInfo-only `sf_contact` rows that meet the deletion criteria (B7) and fail **every** keep rule: delete in Salesforce (`Sfdc.delete_object`), handle local mirror (D16), after writing an audit record (D14).
- `--dry-run`: full evaluation, no writes; emit per-rule decision counts + a CSV of would-be archives/deletions for client sign-off.
- Keep-rule SQL: port Marshall's guard builders from `find_archive_contact.py`, converting the in-memory keep-ID tuples into `EXISTS` subqueries so single-record evaluation stays cheap. Reuse `DEVOUTCOME_NORMALIZATION`.
- Wrap SF deletes and DB writes with the existing lock-retry helper.

### Parallelism

Launcher command (pattern: `launch_sfdc_sync_contact_local_id_shards`) splits `MIN(id)..MAX(id)` of `sp_prospect` into 5 shards and launches 5 AWS Batch jobs on the sequencer queue, each running `zoominfo-exit --from-id --to-id`. Idempotent by design (`archived=0` filter; `delete_object` treats already-deleted as success), so shards can be re-run.

### Teardown (separate PR, after the run — or before, pending client confirmation)

Remove:

- Tables + models + migrations: `sp_prospect_unmatched`, `sp_contact_unmatched`, `sp_lead_unmatched`, `sp_smartlead_prospect_unmatched` (drop via new raw-SQL migration, per repo convention).
- Commands: `five_five_gz_csv_file_process`, `load_five_five_gz_csv_files`, `find_unmatched_lead_prospect`, `find_archived_contact`, `find_archived_lead`, `find_archived_smartlead`, `archive_archived_*`, `delete_archived_contact` + registrations in `app/commands/__init__.py`.
- **Other consumers that must be un-wired**: `app/services/cleanser.py` (cleanses `sp_prospect_unmatched` emails) and `app/commands/deduplicate.py:113` (repoints its `company_id`).

## Delivery Slices

Sliced so each step is independently shippable, reviewable, and strictly less risky than the next. Slices 0–1 are fully unblocked today.

### Slice 0 — read-only evaluator + dry-run report (no writes anywhere)

> **Dev-ready spec: see `zoominfo-exit-slice0-dev.md`** (work packages WP-A / WP-B, frozen interface, reason-code vocabulary, test plan). The summary below is superseded by that doc where they differ.

The core asset: a decision engine that classifies one prospect at a time, plus the `--dry-run` command around it.

- `zoominfo-exit --dry-run --from-id --to-id` typer command; selects `sp_prospect` IDs in batches of 100.
- **5x5 matching — see "Matching against 400M rows" below.** (An earlier draft proposed exploding all UP email columns into a lookup table; at 400M source rows that's a ~2B-row artifact and is rejected.)
- Decision logic per prospect:
  1. Not ZoomInfo-sourced (`zoominfo_contact_id` and `zoominfo_url` both blank) → `SKIP_NOT_ZOOMINFO`.
  2. Already archived → `SKIP_ALREADY_ARCHIVED`.
  3. Any prospect email (`email`, `supplemental_email`) hits the UP email index → `KEEP_5X5_MATCH`.
  4. Email-match to `sf_contact` (undeleted); evaluate all 14 keep rules as EXISTS-based flags in one set-based query per batch → `KEEP_SF_<RULE>` (record every rule that fired, not just the first).
  5. Matched contact exists but is neither kept nor in the delete list → `KEEP_UNCLASSIFIED_SF` (default-keep; sized in the report for the client to rule on).
  6. No defense → `ARCHIVE_CANDIDATE`; if the matched contact is ZoomInfo-only AND (DevPhase ∈ {01 Suspect, 02 Cleansing} OR DevOutcome ∈ {01 Intro, 04 No Response}) AND no keep rule → additionally `DELETE_CANDIDATE`.
- Dry-run output: per-reason-code counts to stdout/log + full decision CSV (prospect id, matched contact sf_id, decision, reason codes) to S3/local path.
- Unit tests against docker MySQL fixtures for every keep rule and reason code.

### Matching against 400M rows (`5x5_universal_person` scale constraint)

Prod facts (2026-07-14): `5x5_universal_person` **400M+ rows**; `sp_prospect` **29.85M rows**; `5x5_universal_person_archive` **empty** (ignored).

**Order of evaluation — cheap defenses first.** Salesforce keep-rule checks are indexed lookups against ~11M `sf_contact` rows; run them *before* any 5x5 work. Only prospects that survive everything else need a 5x5 verdict. Dedupe by email: evaluate each distinct candidate email once per run, not once per prospect.

**5x5 match backend — MySQL primary (decided; OS-only considered and parked):**

- `personal_emails` — already indexed (migration `8b138c25a32e`, `ALGORITHM=INPLACE, LOCK=NONE`). Direct indexed lookup.
- `business_email` — **new migration adds the same INPLACE index**. One-time cost on 400M rows: hours of background IO, no locking; ops precedent is the Dec 2025 migration on this very table. Pre-check with prod ops: disk headroom (tens of GB) and replica-lag tolerance.
- Rationale vs OpenSearch-only: OS (`5x5_universal_person_v4`) is a mirror synced by a pipeline outside this repo. A lagging/incomplete mirror yields *missing* matches → missing defenses → **wrongful archives**, i.e. errors in the destructive direction, which our default-keep posture exists to prevent. MySQL is the source of truth. Also unverified whether `personal_emails` is in the OS documents at all (this codebase only queries `business_email` there).
- **OS as dry-run cross-check (optional, cheap):** during dry-runs, run the same candidate emails against OS and report divergence — validates the mirror and strengthens the client sign-off report. If prod ops vetoes the index build, OS-only becomes plan B, gated on (1) the OS mapping containing `personal_emails.keyword`, (2) a pre-run parity gate (doc count vs row count + random-sample presence check).

### Slice 1 — prospect archive (local-only, reversible)

- Add `--execute` (default off): `ARCHIVE_CANDIDATE` rows get `archived=1, archive_reason='zoominfo exit', archived_at=<today>` — single-row UPDATE by PK, committed one record at a time (client's locking requirement).
- Audit rows written for every archive action.
- Shard launcher command (5 AWS Batch jobs over the ID range, existing `launch_job` pattern).
- Safe to run after dry-run review with Tomas only: it's a flag flip on our own table, reversible by `UPDATE ... SET archived=0 WHERE archive_reason='zoominfo exit'`.

### Slice 2 — Salesforce contact deletion (destructive — gated)

**Blocked on**: client ratification of B8 (delete-list gate, since our default-keep reverses the reviewed code's posture), A4 (deletion universe), and dry-run sign-off (D15).

- `--delete-contacts` flag on top of `--execute`.
- Per `DELETE_CANDIDATE`: write audit record first (full JSON snapshot of the contact row) → `Sfdc.delete_object("Contact", sf_id)` → local `sf_contact.IsDeleted=1` on success. Match on **Salesforce ID** (`sf_id`), never local PKs (the bug in the Feb version).
- Optional `--verify-live`: re-fetch the contact via SOQL immediately before deletion and re-evaluate keep rules on live values (mitigates local-mirror staleness).
- Dedup deletes across shards: two prospects sharing an email can target the same contact — `delete_object` is idempotent and the audit table has a unique key on `sf_id`.

### Slice 3 — teardown of pre-tagging (**merged into Slice 0**, 2026-07-14)

Decision (Tomas): no legacy code hanging around, even in the informational phase — the new code must *replace* Marshall's pipeline in the same PR, not sit next to it. Teardown is now **WP-C of the Slice 0 dev spec** (delete staging commands + models, drop the four `*_unmatched` tables, un-wire `cleanser.py`/`deduplicate.py`, relocate the generic sfdc sync-shard commands). Trade-off accepted: `sp_contact_unmatched` is no longer available as a cross-check against dry-run output — its value was low anyway (point-in-time, and under-populated by the LinkedIn matching bug). This also resolves D17.

### Slice 4 — deferred scope

Salesforce Leads and Smartlead campaign removal, pending C11/C12.

### Audit log

- Table `zoominfo_exit_audit`: `id, run_id, shard, prospect_id, sf_contact_id (unique, nullable), action (archive|delete), reason_codes, contact_snapshot (JSON, delete only), created_at`. Raw-SQL migration per repo convention.
- Written **only for executed actions** (dry-run decisions go to the CSV export instead — at sp_prospect scale, auditing keeps as table rows would dwarf the table).
- Post-run export of the audit table to S3 for permanent retention (SF recycle bin only holds deletes ~15 days).

---

## Open Questions for the Client

**Blocking — the design cannot be finalized without these:**

### A. What does "only traces back to ZoomInfo" mean, precisely?

- **A1. Realtime 5x5 match definition.** ~~Blocker~~ **Decided 2026-07-14**: case-insensitive full match on all UP email columns (see Decisions). Residual caveat for the client: `programmatic_business_emails` are *guessed* patterns (name + domain), so a coincidental hit there keeps a prospect that 5x5 never actually verified — acceptable under default-keep, but it lowers archive coverage. Also confirm whether LinkedIn URL should count as a second match key (prospects with no email currently can't be defended by 5x5 at all).
- **A2.** Does a match in `5x5_universal_person_archive` count as "has a UP ID", or only active UP records?
- **A3.** Prospects with **no** ZoomInfo ID at all are out of scope regardless of UP match — confirm. And should `data_source_id` play any role in "only traces to ZoomInfo"?
- **A4.** For `sf_contact`, is the deletion universe (a) contacts email-matched to a ZoomInfo-only prospect (Marshall's approach), or (b) any contact with `ZoomInfo_Contact_Id__c` populated? (b) includes contacts with no local prospect at all.
- **A5.** Should prospect↔contact association use email equality only, or also our `sp_contact_prospect` link table? When one email matches multiple SF records, we keep the prospect if *any* match is protected — confirm.

### B. The keep/delete rule sets don't line up

- **B6. Direct contradiction on "Intro":** the delete list says delete "Intro", the keep document keeps DevOutcome `01 Intro 1/2/3` (and production also has plain `01 Intro`). The reviewed code keeps `01 Intro 1/2/3` and deletes plain `01 Intro` — confirm that stands.
- **B7. Field mixing:** "Cleansing"/"Suspect" are **DevPhase** values (`02 Cleansing`, `01 Suspect`); "Intro"/"No response" are **DevOutcome** values (`01 Intro`, `04 No Response`). Confirm intended rule, e.g. `DevPhase ∈ {Suspect, Cleansing} OR DevOutcome ∈ {Intro, No Response}`.
- **B8. Is the delete list exhaustive?** A ZoomInfo-only contact whose phase/outcome is in *neither* list (`Research Dead End`, `Not a Fit`, `Out Of Business`, `Duplicate`, …) — delete or keep? Same for LeadSource values outside both lists. Note: the **reviewed code is default-delete** (anything failing every keep rule is a candidate; the delete list is never consulted). The keep document's own hedge ("better to do any that aren't clearly NOT what we sourced") suggests default-keep. These conflict — we need an explicit ruling, and the dry-run report will size the "neither" bucket either way. **Decided 2026-07-14 (our side): build default-KEEP** — deletion additionally requires the delete-list gate (DevPhase ∈ {Suspect, Cleansing} OR DevOutcome ∈ {Intro, No Response}). Client must ratify, since this will produce materially fewer deletions than the reviewed default-delete code would have.
- **B9. Prospect keep list vs contact keep doc.** The prospect-archive keep list is much narrower than the SF keep document. If a matched SF contact is kept for a reason outside that list (Client Contact record type, opportunity POC, has calls…), should the prospect also be kept, or archived anyway?
- **B10. Value normalization sign-off:** "Appt" = Appt A/B/C (+ Appt Sold?); "TOC" = `07 TOC Open`; "Lead Wait" = `02 Wait`; "Info" = `Info`. Confirm expansions.

### C. Leads and Smartlead — in or out? (**deferred 2026-07-14** — out of scope for the current slices, answers still wanted eventually)

- **C11.** Your keep document includes rules for Salesforce **Leads**, but the action items only cover Contacts. Should ZoomInfo-only Leads be handled in this run too — and if so, deleted or just archived?
- **C12.** Marshall's original code also removed archived prospects from **Smartlead** email campaigns. Your requirements don't mention this — should we keep that behavior or drop it?

### D. Execution, freshness, safety

- **D13. Mirror freshness.** Keep rules read our local `sf_*` mirror, which is synced periodically — same staleness class as pre-tagging, shorter lag. Is a full sync immediately before the run sufficient, or should the command **re-verify each contact live via SOQL** right before deleting (slower, eliminates the sync-to-delete race)?
- **D14. Volumes & API strategy.** Rough `sp_prospect` row count and expected SF deletion count? 5 parallel jobs doing single-record REST deletes could exhaust the org's daily API limit; large volumes argue for Bulk API on the SF-delete leg. Also: SF recycle bin holds deletes ~15 days only — we strongly recommend a pre-deletion **audit export** (IDs + key fields to S3), since after teardown no table records what was removed.
- **D15. Sign-off gate.** Confirm process: dry-run report (per-rule counts + sample CSV) → client approval → destructive run. Who approves?
- **D16.** When a contact is deleted in Salesforce, soft-delete the local `sf_contact` mirror row immediately (`IsDeleted=1`), or let the next sync pick it up?
- **D17.** ~~Teardown before or after the run?~~ **Resolved 2026-07-14**: teardown ships with Slice 0 as WP-C (see Slice 3 note above).
