# ZoomInfo Exit — Test-Batch Salesforce Contact Deletion (dev-ready)

**Status: approved for build (2026-08-21, Tomas).** Client signed off on **this run only**: hard-delete a capped test batch of Salesforce contacts (target: 500). No prospect archiving, no Leads, no Smartlead, **no audit table**. The delivery slices in `zoominfo-exit-archive.md` written after Slice 0 are preliminary — this document supersedes them for the deletion work; do not carry over their details without re-deciding.

**Why a live test**: beyond proving the mechanics, the client wants to observe what the SF org *does* when contacts are deleted — triggers, workflows, automations. A bounded batch caps the blast radius (e.g. we must not find out via 360K automated emails).

Base: branch `zoominfo-write-ops` (even with `upstream/master`; Slice 0 dry-run engine merged).

## Decisions (2026-08-21, Tomas)

1. Flags: `--delete-contacts` (mode) + `--max-deletes N` — not a single combined flag.
2. When the cap is reached, **stop the entire run** (don't continue evaluating).
3. **Successes** count toward the cap; failures are logged and reported separately.
4. Candidate selection: **first N** delete candidates scanning up from `--from-id`. No sampling.
5. **No `--verify-live`**: a 2xx from the Salesforce delete is authoritative; the local mirror is the evaluation authority.
6. **No audit table** (revises decision #5 of 2026-07-14). The durable record is: two new columns on `sf_contact`, the retained mirror row (a permanent snapshot — see "Why the mirror row survives" below), and the per-run deletions CSV.
7. Sign-off covers this capped test batch only. Prospect archiving remains blocked.

---

## WP-1 — migration + model: deletion provenance columns on `sf_contact`

Two nullable columns documenting **our** hard delete (as opposed to `IsDeleted`, which mirrors whatever Salesforce reports):

| Column | Type | Meaning |
|---|---|---|
| `deleted_at` | `DATETIME NULL` | UTC timestamp of our successful Salesforce delete call |
| `delete_reason` | `VARCHAR(255) NULL` | `'zoominfo exit'` — same value convention as `sp_prospect.archive_reason` |

- Raw SQL via `op.execute()` (repo convention). Both `ADD COLUMN`s in one `ALTER TABLE ... , ALGORITHM=INSTANT` — nullable, no default, so INSTANT succeeds on Aurora 3.10 (8.0.42-compat); no rebuild of the ~11M-row table. Downgrade: `DROP COLUMN` (also INSTANT ≥ 8.0.29).
- Column comments must state provenance, e.g. `deleted_at`: "When uDab hard-deleted this contact in Salesforce (UTC); NULL = never deleted by us".
- Model: add both to `SfContact` (`app/models/sf_contact.py`) as snake_case `Mapped[Optional[...]]` columns next to the other locally-owned fields (`replied_at` block).
- **No index** on either column. The test batch writes ≤ ~500 rows; the rollback/verification queries below are one-off scans — acceptable.

### Why the mirror row survives as a snapshot (verified 2026-08-21)

`process_contact_record` (`app/commands/sfdc/sync.py:290`) upserts by `sf_id`, writes only its own mapped columns, and never deletes local rows — the new columns are untouched by sync. The sync SOQL runs against `/queryAll`, so for ~15 days post-delete it still returns the contact (recycle bin) with `IsDeleted=1`; after purge the record drops out of results and the local row goes permanently stale. Net: the local row is the lasting snapshot of what was deleted. Known quirk, accepted: if someone undeletes the contact in SF, sync flips `IsDeleted` back to 0 while `deleted_at`/`delete_reason` stay — correct history, not a bug.

---

## WP-2 — CLI: delete mode

### Interface

```
zoominfo-exit --delete-contacts --max-deletes 500 --from-id 0 --to-id <int>
              [--batch-size 100] [--output-dir ...] [--s3-bucket ...] [--whitelist-url ...]
```

- `--delete-contacts` (bool, default false): execute Salesforce contact deletions for `DELETE_CANDIDATE` contacts.
- `--max-deletes` (int): hard cap on **successful** deletions. **Required** when `--delete-contacts` is set (no unbounded default), must be > 0; passing it without `--delete-contacts` is an error.
- Mode validation: **exactly one** of `--dry-run` / `--delete-contacts`. Neither → the existing refusal (`cli.py:59`) stands; both → abort non-zero. The "no silent execute" property is preserved.
- Session endpoint: already keyed on `dry_run` (`cli.py:100`) — delete mode gets the **writer** automatically. Keep the `innodb_read_only` log line as proof of which instance the run hit.
- `--whitelist-url` stays optional in code; the runbook mandates it for delete runs (defense parity with the signed-off dry runs).
- `zoominfo-exit-launch` is **unchanged** (dry-run shards only). A capped delete run is a single process by design — a global cap doesn't shard.

### Execution flow

Decision logic is untouched — the engine already computes per-contact `DELETE_CANDIDATE` (`engine.py:58`: ZoomInfo-tagged + delete-gated + zero keep rules; `IsDeleted=0` guaranteed by the query). Execution is a thin layer driven from the CLI loop after each batch's evaluations:

Per batch, after `evaluate_prospects` + report rows are written:

1. Collect `delete_candidate_sf_ids` in prospect-id order; dedupe against a run-level `processed_sf_ids` set (two prospects can share an email and target the same contact; memory is bounded by cap + failures).
2. For each new sf_id, while successes < `--max-deletes`:
   - `Sfdc().delete_object("Contact", sf_id)` — returns truthy on 2xx and treats already-deleted as success (idempotent).
   - **On success**: `UPDATE sf_contact SET IsDeleted = 1, deleted_at = :now_utc, delete_reason = 'zoominfo exit' WHERE sf_id = :sf_id`, committed **immediately, one record at a time** (the client's single-record-commit requirement). A crash between the SF delete and the local write self-heals: a re-run re-attempts the delete, gets idempotent success, and marks the row.
   - **On failure**: log with sf_id + error, write a `failed` row to the deletions CSV, do **not** count toward the cap, continue. **Circuit breaker**: 5 *consecutive* failures → abort the run non-zero (an expired token or API outage must not chew through the candidate list).
3. When successes reach the cap: log it, finalize reports, exit 0 — no further batches are evaluated.

The one `Sfdc` instance is created at run start (plain `Sfdc()`, the repo-wide pattern) and reused; its built-in retry/auth handling applies.

### Reporting

The per-prospect decisions CSV and summary JSON are unchanged in shape. Deletion outcomes get their own artifact:

- **`deletions_{run_id}.csv`** — one row per delete attempt: `sf_id, prospect_id, outcome (deleted|failed), error, deleted_at`. Streamed + flushed per row like the decisions CSV; joins `report_files` so `--s3-bucket` uploads it too. This file is the run-level deletion record (the run_id ties it to a specific invocation).
- **Summary JSON** gains: `delete_mode` (bool), `max_deletes`, `contacts_deleted`, `contacts_delete_failed`, `cap_reached` (bool). Stdout table gets matching lines. All keys present in dry-run summaries too (false/0/null) so the schema stays stable.

---

## Runbook (test batch)

1. Merge + deploy — CI applies the migration automatically on merge to `stage`/`prod`.
2. Confirm the nightly SFDC contact sync ran recently — the mirror is the evaluation authority (no live verification, by decision #5).
3. Single process (AWS Batch one-off job or dev box over `aiqvpn`):
   `zoominfo-exit --delete-contacts --max-deletes 500 --from-id 0 --to-id <max sp_prospect.id> --whitelist-url s3://abstrakt-intelligence/zoominfo-exit/whitelist.csv --s3-bucket abstrakt-intelligence`
4. Pre-run: `OPTIMIZE TABLE sf_project` + the LIKE/MATCH parity check from `zoominfo-exit-archive.md` → "Pre-run checklist" (the fulltext keep rule is a defense; this run is destructive).
5. **Observe the org** — the point of the test. Coordinate with the client to watch for trigger/automation side effects (email sends, workflow logs) immediately after the run.
6. Verify: in SF, the deleted contacts sit in the recycle bin (~15 days); locally,
   `SELECT COUNT(*) FROM sf_contact WHERE delete_reason = 'zoominfo exit'` matches `contacts_deleted`.
7. Rollback window: ~15 days via SF undelete (UI/API). Local revert if ever needed:
   `UPDATE sf_contact SET IsDeleted = 0, deleted_at = NULL, delete_reason = NULL WHERE delete_reason = 'zoominfo exit'`.

## Tests (extend the existing fixture suite; stub `Sfdc`)

- Flag validation: `--dry-run --delete-contacts` errors; `--delete-contacts` without `--max-deletes` errors; `--max-deletes 0` / negative errors; `--max-deletes` without `--delete-contacts` errors.
- Cap enforcement: run stops mid-batch at exactly N successes; remaining candidates and batches untouched.
- Dedupe: the same contact reachable from two prospects is deleted once and counted once.
- Failure handling: a failed delete doesn't count toward the cap, doesn't write local columns, lands as `failed` in the deletions CSV; 5 consecutive failures abort non-zero; a success resets the streak.
- Local marking: after a stubbed success, the row has `IsDeleted=1`, `deleted_at` set, `delete_reason='zoominfo exit'`, committed (visible from a second session).
- Idempotent re-run: candidate already `IsDeleted=1` locally is not re-selected (engine query excludes it).
- Dry-run regression: `--dry-run` makes zero `Sfdc` calls and zero writes; summary JSON carries the new keys as false/0.

## Out of scope (explicitly)

Prospect archiving (`--execute`), `zoominfo_exit_audit` table (rejected), Leads, Smartlead, `--verify-live`, shard-launcher changes, indexes on the new columns. Do not build placeholders.
