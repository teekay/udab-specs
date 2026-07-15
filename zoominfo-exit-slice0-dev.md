# ZoomInfo Exit — Slice 0 Implementation Spec (dev-ready)

> **Status 2026-07-15: IMPLEMENTED** on `udab-server` branch `zoominfo-contact-cleanup` (uncommitted, pending Tomas's review + PR). All three WPs delivered and verified: 70 new tests green, full suite 3357 passed (1 legacy cleanser test removed — it asserted the deleted `unmatched=True` path), migration chain `9d4e1c7a2f38` (business_email index) → `7e2a9c4d1b30` (drop staging tables) upgrade/downgrade/upgrade verified, CLI smoke-tested end-to-end (dry-run guard exits 1; real dry run over ids 0-200 produced CSV + summary), repo-wide legacy greps clean.

**Repo**: `udab-server`. **Parent doc**: `zoominfo-exit-archive.md` (context, decisions, open questions).
**Nature of this slice: strictly informational.** No row in any table is modified, no Salesforce call is made, nothing is deleted or archived. The deliverable is a `--dry-run`-only CLI that classifies prospects and produces a report the CTO can review.

Split into three work packages. WP-A and WP-B share a frozen interface and build in parallel; WP-C runs **after** both are code-complete on the same branch:

- **WP-A** — 5x5 email matcher (+ optional index migration)
- **WP-B** — decision engine, keep rules, dry-run CLI, report writer
- **WP-C** — legacy teardown: delete Marshall's pre-tagging pipeline, relocate the pieces we keep, drop the staging tables

**No duplication policy**: this slice *replaces* the legacy pipeline, it does not sit next to it. WP-B copies the client-reviewed constants into the new package; WP-C then deletes their source file — the net effect across the PR is a **move**. After WP-C, nothing in `app/` references the unmatched tables or the legacy staging commands.

---

## Ground rules (both packages)

- Python 3.12, FastAPI codebase, SQLAlchemy 2 async (`AsyncSessionLocal` from `app.db`), typer commands. Match surrounding code style.
- New code lives in a new package **`app/commands/zoominfo_exit/`**. WP-A/WP-B must not *import* from `app/commands/five_five/` (WP-C deletes parts of it); they copy constants where instructed, and WP-C removes the originals so the copy becomes a move.
- Migrations are raw SQL via `op.execute()` (repo convention — see `migrations/versions/8b138c25a32e_*.py` as the model).
- No writes: this slice performs `SELECT`s only against MySQL. The only files written are the local report files (and optional S3 upload of those files).
- Tests run via `docker compose exec fastapi pytest tests/<file>` from the `udab-server` root. Follow the style of existing `tests/test_*.py`.
- Emails are compared **case-insensitively on full-string equality**: normalize in Python with `email.strip().lower()`; MySQL's `*_ci` collation handles the SQL side. Treat `''` and whitespace-only as NULL.

### Scale context (why the design looks like this)

- `sp_prospect`: 29,853,292 rows (prod)
- `sf_contact`: ~11M rows (prod)
- `5x5_universal_person`: 342,859,835 rows (prod) — updated **once a month via bulk load**
- `5x5_universal_person_archive`: empty — **ignore entirely**

---

## Shared vocabulary (used by both packages; define once in `app/commands/zoominfo_exit/codes.py`)

### Decisions (exactly one per evaluated prospect)

| Decision | Meaning |
|---|---|
| `SKIP` | Not in scope for the ZoomInfo exit |
| `KEEP` | In scope, but at least one defense exists |
| `ARCHIVE` | In scope, no defense — would be archived in a later slice |

### Reason codes (a decision carries ≥1 of these)

`SKIP` reasons:

| Code | Condition |
|---|---|
| `NOT_ZOOMINFO_SOURCED` | `zoominfo_contact_id` AND `zoominfo_url` both blank/NULL |
| `ALREADY_ARCHIVED` | `sp_prospect.archived = 1` |

`KEEP` reasons (record **every** one that fires, not just the first — the report must show all defenses):

| Code | Condition |
|---|---|
| `FIVE_X_FIVE_BUSINESS_EMAIL` | a prospect email == `5x5_universal_person.business_email` |
| `FIVE_X_FIVE_PERSONAL_EMAIL` | a prospect email == `5x5_universal_person.personal_emails` |
| `SF_RT_CLIENT_CONTACT` | matched contact RecordTypeId = `012G0000000qeQxIAI` |
| `SF_RT_BILLING_CONTACT` | matched contact RecordTypeId = `0124A000001cpAqQAI` |
| `SF_DEVOUTCOME_PROTECTED` | contact RT ∈ internal-pipeline set AND normalized DevOutcome ∈ keep set |
| `SF_TASK_CALL_DISPOSITION` | contact RT ∈ internal-pipeline set AND an undeleted `sf_task` (via `WhoId` or `Contact__c`) has CallDisposition ∈ {appointment, contact, kdm-pitched} |
| `SF_EVENT_WHOID` | contact RT = Abstrakt Pipeline AND an undeleted `sf_event.WhoId` references it |
| `SF_OPPORTUNITY_CONTACT` | contact RT = Abstrakt Pipeline AND an undeleted `sf_opportunity.Contact_ID__c` references it |
| `SF_LEADSOURCE_ABSTRAKT` | contact RT = Abstrakt Pipeline AND LeadSource ∈ Abstrakt keep list |
| `SF_LEADSOURCE_TALENT` | contact RT = Talent Solutions AND LeadSource ∈ Talent keep list |
| `SF_CONTACT_CALLS_GT0` | `of_Contact_Calls__c > 0` |
| `SF_LAST_CONTACT_SET` | `Last_Contact_Datetime__c IS NOT NULL` |
| `SF_LIST_TAG_A` | `List_Tag__c = 'a'` (ci) |
| `SF_PRIMARY_LIST_SOURCE` | `Primary_List_Source__c` not blank and ∉ {internal salesforce, zoominfo, abstrakt database} |
| `SF_PROJECT_REFERRAL_WEBLEAD` | contact's `Project__c` is an undeleted `sf_project` whose Name fulltext-matches "pipeline referrals" or "web leads" |
| `SF_HR_SOURCE_NOT_ZOOMINFO` | contact RT = HR Generic AND `Source__c` ∉ {zoom info, zoominfo} **including NULL/blank Source__c** (deliberate deviation from the legacy code, which dropped NULLs via three-valued logic — default-keep) |
| `SF_UNCLASSIFIED` | a matched, undeleted contact exists; no keep rule fired; contact is NOT delete-gated (see below). Default-keep bucket — the client will review its size |

`ARCHIVE` reasons:

| Code | Condition |
|---|---|
| `NO_PROSPECT_EMAIL` | prospect has no usable email → no defense is even possible. Kept as a distinct code so the report sizes this cohort |
| `NO_DEFENSE` | emails exist, but no 5x5 hit and no SF defense |

### Delete-candidate flag (per matched contact, informational only in this slice)

Independently of the prospect decision, each matched contact is classified. A contact is a `DELETE_CANDIDATE` iff **all** of:

1. `ZoomInfo_Contact_Id__c` is non-blank (the contact itself is provably ZoomInfo-tagged — conservative pending open question A4), and
2. it is **delete-gated**: normalized DevPhase ∈ {`suspect`, `cleansing`} OR normalized DevOutcome ∈ {`intro`, `no response`} (normalization below), and
3. **no** keep rule fired for it, and
4. `IsDeleted = 0`.

A contact matched via the prospect but failing condition 1 or 2 is simply not a delete candidate (default-keep); the prospect's own decision is unaffected by this flag.

### Normalization

- **DevOutcome canonicalization**: copy `DEVOUTCOME_NORMALIZATION` and `KEEP_CONTACT_DEVOUTCOME_VALUES` verbatim from `app/commands/five_five/find_archive_contact.py:84-116` (these are client-reviewed). Apply the mapping (ci compare), then membership-test against the keep set.
- **Delete-gate normalization** (used only for gate condition 2 above): `trim → lower → strip a leading `NN ` two-digit-plus-space prefix if present`. So `01 Suspect` → `suspect`, `02 Cleansing` → `cleansing`, `01 Intro` → `intro`, `04 No Response` → `no response`. `01 Intro 1` → `intro 1` — correctly NOT gate-matched (it's in the keep set anyway).
- **Prospect emails considered**: `sp_prospect.email` and `sp_prospect.supplemental_email`, each normalized; used identically for the 5x5 check and the `sf_contact` match.

### Constants to copy (do not import) from `app/commands/five_five/find_archive_contact.py`

Record type IDs (lines 27-30), `ABSTRAKT_KEEP_CONTACT_SOURCE_VALUES`, `TALENT_KEEP_CONTACT_SOURCE_VALUES`, `KEEP_CALL_DISPOSITIONS`, `KEEP_PRIMARY_LIST_SOURCE_EXCLUSIONS`, `KEEP_PROJECT_NAME_TOKENS`, `DEVOUTCOME_NORMALIZATION`, `KEEP_CONTACT_DEVOUTCOME_VALUES`, and `INTERNAL_PIPELINE_RECORD_TYPES` (which references `Sfdc.GENERIC_PIPELINE_RECORD_TYPE` / `Sfdc.ABSTRAKT_PIPELINE_RECORD_TYPE` — importing those two from `app.services.sfdc` is fine). Put them in `app/commands/zoominfo_exit/constants.py` with a comment pointing at the origin.

---

## WP-A — 5x5 email matcher

### Files

- `app/commands/zoominfo_exit/matcher.py`
- `migrations/versions/<rev>_add_index_business_email_5x5.py`
- (small helper) `upload_local_file_to_s3(bucket: str, key: str, local_path: str) -> bool` added to `app/services/aws.py`, implemented with `_get_s3_client().upload_file(...)` — streaming, NOT the existing whole-string `put_object` helper.
- `tests/test_zoominfo_exit_matcher.py`

### `matcher.py`

```python
class FiveFiveEmailMatcher:
    """Answers: which of these emails appear in 5x5_universal_person
    (business_email or personal_emails)? Read-only."""

    async def match(self, db: AsyncSession, emails: set[str]) -> dict[str, "FiveFiveMatch"]:
        ...

@dataclass(frozen=True)
class FiveFiveMatch:
    up_id: str
    matched_column: str  # "business_email" | "personal_emails"
```

Behavior:

- Input emails are already normalized (lowercase, trimmed, non-empty) — assert/ignore otherwise.
- Chunk the input (chunks of ≤500) and run per chunk, two indexed lookups (UNION ALL is fine):
  `SELECT business_email AS email, up_id, 'business_email' AS col FROM 5x5_universal_person WHERE business_email IN (:chunk)` and the same for `personal_emails`.
- Return the first match per email; when both columns hit, prefer `business_email`.
- Must not load more than the matched rows (never scan). No ORM entity hydration — use `select()` on columns or `text()`.

### Migration (index on `business_email`)

Model it byte-for-byte on `migrations/versions/8b138c25a32e_add_indexes_to_5x5_universal_person_.py`:

```sql
ALTER TABLE `5x5_universal_person`
  ADD INDEX ix_5x5_universal_person_business_email (business_email)
  COMMENT 'ZoomInfo-exit: exact-match lookups',
  ALGORITHM=INPLACE, LOCK=NONE
```

with the symmetric `DROP INDEX` downgrade. In the migration docstring, note: (a) ~343M rows — hours of background IO on prod, no locking; (b) the table is bulk-loaded monthly — **ops must confirm the load procedure preserves secondary indexes** (if the load recreates the table, this index must be added to the load pipeline); (c) whether to merge this migration is explicitly a **PR-reviewer decision** — the matcher works without it, just slowly (`personal_emails` is already indexed by `8b138c25a32e`; only `business_email` lookups would scan).

### Tests (WP-A)

- Match on `business_email`, on `personal_emails`, on both (business wins), case-insensitive match, no match, empty input, >500 emails (chunking).
- Fixtures: insert a handful of `UniversalPerson` rows via `AsyncSessionLocal`, clean up after. `up_id` is the PK; `last_confirmed_date` is non-nullable — supply it.

---

## WP-B — decision engine + dry-run CLI + report

### Files

- `app/commands/zoominfo_exit/codes.py` — decisions/reason codes (shared vocabulary above)
- `app/commands/zoominfo_exit/constants.py` — copied constants (see above)
- `app/commands/zoominfo_exit/rules.py` — keep-rule SQL builders
- `app/commands/zoominfo_exit/engine.py` — batch evaluation
- `app/commands/zoominfo_exit/report.py` — CSV + summary writers
- `app/commands/zoominfo_exit/cli.py` — typer entrypoints
- registration in `app/commands/__init__.py` (import + `app.command(...)`, follow the existing pattern in that file)
- `tests/test_zoominfo_exit_rules.py`, `tests/test_zoominfo_exit_engine.py`

### CLI

```
zoominfo-exit
  --dry-run            (REQUIRED in this slice; the command must abort with a clear
                        error if --dry-run is not passed — the execute path does not exist yet)
  --from-id INT        exclusive lower bound on sp_prospect.id  (default 0)
  --to-id INT          inclusive upper bound on sp_prospect.id  (required)
  --batch-size INT     prospects per evaluation batch           (default 100)
  --output-dir PATH    where report files land                  (default ./zoominfo_exit_reports)
  --s3-bucket TEXT     optional; if set, upload report files to s3://<bucket>/zoominfo-exit/<run_id>/
```

`run_id` = `{from_id}-{to_id}-{UTC timestamp yyyymmddHHMMSS}`.

### Engine flow (per batch of `--batch-size` prospect IDs, `id > from_id AND id <= to_id`, ordered)

1. Load the batch's prospects (id, email, supplemental_email, zoominfo_contact_id, zoominfo_url, archived).
2. Classify `SKIP`s (reason codes above) in Python.
3. Collect normalized distinct emails from the survivors. Maintain a **per-run in-memory cache** `email -> FiveFiveMatch | None` so each distinct email hits WP-A's matcher at most once per run; call `FiveFiveEmailMatcher.match()` only for uncached emails.
4. Prospects with a 5x5 hit → `KEEP` with the 5x5 reason code (+ record `up_id`). **Do not stop** — still evaluate SF matches for them? **No.** Short-circuit: 5x5-defended prospects skip the SF evaluation (cheaper; the report notes only the firing defense). Prospects with no usable email → `ARCHIVE` / `NO_PROSPECT_EMAIL`.
5. Remaining prospects: one set-based query joining their emails to `sf_contact` (`Email IN (...) AND IsDeleted = 0`) selecting, per contact row: `sf_id`, `Email`, `RecordTypeId`, `DevPhase__c`, `DevOutcome__c`, `ZoomInfo_Contact_Id__c`, plus **one boolean column per keep rule**. Simple column predicates are plain SQL `CASE`/boolean expressions; the four relational rules (task, event, opportunity, project) are **correlated `EXISTS` subqueries** — port the conditions from the legacy guard builders (`find_archive_contact.py:245-321` and `:397-446`) but do NOT pre-load ID lists into memory (that was the legacy staging-scale approach; at batch scale EXISTS is correct). The project rule keeps the `MATCH(Name) AGAINST(...)` fulltext predicate (`ft` index exists on `sf_project.Name`) inside an `EXISTS (SELECT 1 FROM sf_project WHERE sf_id = contact.Project__c AND IsDeleted = 0 AND MATCH...)`.
6. Aggregate per prospect in Python:
   - any contact row with ≥1 keep flag → `KEEP` + all fired codes;
   - else any contact row not delete-gated → `KEEP` / `SF_UNCLASSIFIED`;
   - else (no contact rows, or every contact delete-gated) → `ARCHIVE` / `NO_DEFENSE`.
   - Compute `DELETE_CANDIDATE` per contact per the shared definition; attach the qualifying `sf_id`s to the prospect's row.
7. Append one CSV row per evaluated prospect; update counters; log progress per batch (`logger.info`, follow `AppLogger` usage in the codebase).

Connection/session handling: reuse one `AsyncSessionLocal()` session for the whole run (read-only); no transactions to manage.

### Report format

`decisions_{run_id}.csv` — one row per evaluated prospect (including SKIPs):

```
prospect_id, prospect_email, supplemental_email, zoominfo_contact_id,
decision, reason_codes (| separated), matched_up_id, five_x_five_column,
matched_contact_sf_ids (|), delete_candidate_sf_ids (|),
contact_record_types (|), contact_dev_phases (|), contact_dev_outcomes (|)
```

`summary_{run_id}.json` — run metadata (from/to, started/finished, row counts) + a counter per decision and per reason code + a counter of `DELETE_CANDIDATE` contacts. Also print the summary as an aligned table to stdout at the end.

Write the CSV **streamed** (plain `csv` module, append as you go) — 30M-row runs must not buffer in memory. If `--s3-bucket` is set, upload both files at the end via WP-A's `upload_local_file_to_s3`.

### Shard launcher (small, last)

`zoominfo-exit-launch --to-id INT --shards INT=5 --batch-size INT=100 --s3-bucket TEXT` — splits `(0, to_id]` into N equal ID ranges and calls `app.services.aws.launch_job(db, "zoominfo-exit", args, queue="sequencer")` per shard with `dry-run: True`. Port the loop shape from `launch_sfdc_sync_contact_local_id_shards` (`find_archive_contact.py:831-891`). Job-definition wiring for AWS Batch is ops-side and out of scope — just launch with the right arg names (kebab-case, as in the legacy launcher).

### Tests (WP-B)

DB-backed (docker MySQL via `AsyncSessionLocal`, fixtures inserted/cleaned per test):

- One test per keep rule: a prospect + contact fixture engineered so exactly that rule fires; assert decision `KEEP` and the exact reason code. (14 tests — table-driven is fine.)
- `SF_UNCLASSIFIED` default-keep: contact with DevOutcome `Research Dead End`, no other signals → `KEEP` / `SF_UNCLASSIFIED`, not a delete candidate.
- Delete gate: contact with `ZoomInfo_Contact_Id__c` set + DevPhase `01 Suspect`, no keep rules → prospect `ARCHIVE` / `NO_DEFENSE`, contact in `delete_candidate_sf_ids`. Variants: gate via DevOutcome `04 No Response`; gated but `ZoomInfo_Contact_Id__c` blank → NOT a delete candidate; gated but one keep rule fires → `KEEP`.
- `SKIP` paths, `NO_PROSPECT_EMAIL` path, multi-contact aggregation (one defended + one gated → `KEEP`, and the gated one still NOT a delete candidate — a kept prospect produces no delete candidates? **No — it does**: the delete flag is per-contact and independent; assert this explicitly), 5x5 short-circuit (matcher stubbed), email dedup cache (matcher called once for a repeated email).
- Pure-unit: delete-gate normalization (`01 Suspect`→`suspect`, `01 Intro 1`→`intro 1`, etc.), DevOutcome canonicalization mapping.
- CSV/summary golden test on a tiny range.

Stub `FiveFiveEmailMatcher` in engine tests (inject it — the engine takes the matcher as a constructor arg) so WP-B tests don't depend on WP-A.

---

## WP-C — legacy teardown (run after WP-A + WP-B are code-complete, same branch)

Marshall's pre-tagging pipeline is superseded by WP-A/WP-B. Remove it entirely; relocate the two pieces that are unrelated to pre-tagging and still valuable.

### 1. Relocate (keep, new home) — from `app/commands/five_five/find_archive_contact.py`

Move **unchanged** into a new `app/commands/sfdc/sync_shards.py` (they are generic Salesforce-mirror sync tooling, not pre-tagging; Slice 1/2 will also reuse the retry helper):

- `process_batch_sfdc_sync_contact_local_id_range`, `launch_sfdc_sync_contact_local_id_shards`
- their private helpers: `_count_sf_contact_rows_in_range`, `_load_sf_contact_ids_in_range`, `_build_contact_id_chunk_query`, `_process_contact_record_with_retry`, `_is_retryable_mysql_lock_error`
- constants `CONTACT_WRITE_MAX_RETRIES`, `CONTACT_WRITE_BASE_DELAY_SECONDS`

Update their imports in `app/commands/__init__.py` to the new module path; the registered command names must not change (AWS Batch job definitions reference them).

### 2. Delete — files

- `app/commands/five_five/find_archive_contact.py` (after step 1 extraction)
- `app/commands/five_five/find_gz_csv_unmatched.py` (all three commands in it are pre-tagging: `five_five_gz_csv_file_process`, `load_five_five_gz_csv_files`, `find_unmatched_lead_prospect`)
- Models: `app/models/contact_unmatched.py`, `app/models/lead_unmatched.py`, `app/models/prospect_unmatched.py`, `app/models/smartlead_prospect_unmatched.py`

Other files in `app/commands/five_five/` (company/naics/sic/zipcode syncs, `confirm_5x5_universal_person`, `import_cookie_sync_visitor_tags`) are unrelated — leave them.

### 3. Un-wire — registrations and consumers

- `app/commands/__init__.py`: remove imports + registrations of `find_archived_contact`, `find_archived_lead`, `find_archived_smartlead`, `delete_archived_contact`, `archive_archived_contact`, `archive_archived_lead`, `archive_archived_smartlead`, `five_five_gz_csv_file_process`, `load_five_five_gz_csv_files`, `find_unmatched_lead_prospect` (two blocks: ~lines 53-67 and ~155-170).
- `app/services/cleanser.py`: remove the `ProspectUnmatched` cleansing method (~line 226 on) and its import; find and remove its call sites (grep for the method name — likely a registered command or route).
- `app/commands/deduplicate.py`: remove the `ProspectUnmatched` import (line 14) and the `company_id` repoint statement (line 113).
- Then repo-wide grep for `ContactUnmatched|LeadUnmatched|ProspectUnmatched|SmartleadProspectUnmatched` and for each deleted command name — must only hit `migrations/versions/` (history stays untouched).

### 4. Drop the staging tables — one new migration

Raw SQL via `op.execute()`: `DROP TABLE IF EXISTS` for `sp_contact_unmatched`, `sp_lead_unmatched`, `sp_smartlead_prospect_unmatched`, `sp_prospect_unmatched`. Downgrade recreates them by copying the CREATE TABLE definitions from the original migrations (`d0d50832b8c8`, `0c8b2c7d3a4e`, `c1d2e3f4a5b6`, `56fd1efb8d4f`/`e26aadb0bad4`). Do **not** modify or delete any existing migration file.

### 5. Leave alone

- The `sf_lead` archive columns (migration `1a9c4e7b2d5f`) — Leads are deferred scope; dropping columns is riskier than keeping them idle.
- All `sf_*` models, `Sfdc` service, `smartlead` service.
- Historic migrations, including the index migrations on the staging tables (they become no-ops once tables are dropped; history is append-only).

---

## Acceptance checklist (all packages)

- [ ] `docker compose exec fastapi pytest tests/test_zoominfo_exit_*.py` green
- [ ] `zoominfo-exit --dry-run --from-id 0 --to-id 1000` runs against the local dev DB, produces CSV + JSON summary, exits 0
- [ ] Running without `--dry-run` aborts with a non-zero exit and an explanatory message
- [ ] Zero writes: no INSERT/UPDATE/DELETE statements anywhere in the new package (grep-verifiable), except the report files on disk
- [ ] No imports from `app.commands.five_five`
- [ ] The 14 keep rules are covered by tests one-to-one
- [ ] Existing test suite still green
- [ ] WP-C: repo-wide grep for the four `*Unmatched` model names and each deleted command name hits only `migrations/versions/`
- [ ] WP-C: relocated sync-shard commands still registered under their original command names
- [ ] After WP-C, the constants copied by WP-B exist in exactly one place (`app/commands/zoominfo_exit/constants.py`)

## Explicitly out of scope for this slice

Archiving (`--execute`), Salesforce deletion, the audit table, Smartlead, `sf_lead` handling (including its idle archive columns), OpenSearch cross-check, `--verify-live`. Do not build placeholders for them beyond the `--dry-run` guard.
