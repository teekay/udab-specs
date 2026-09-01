---
kind: spec
status: done
area: zoominfo-exit
updated: 2026-08-24
repos: [udab-server]
summary: "--dry-run --dump-delete-list: per-shard delete-candidate CSV for an admin Data Loader delete, plus a merge script."
---

# ZoomInfo Exit — Delete-Candidate List Dump (`--dry-run --dump-delete-list`)

**Status: built (2026-08-24, Tomas).** Direction change from `delete-test-batch.md`: instead of (or before) uDab deleting contacts via the API, we hand the SF admin a list of delete-candidate contacts for a bulk delete (Data Loader). That spec's `--delete-contacts` mode is untouched and remains available.

## Decision

The list is a **reporting artifact of the dry run**, not a third execution mode. Mode validation is unchanged (exactly one of `--dry-run` / `--delete-contacts`; "no silent execute" preserved). A new additive flag, valid only with `--dry-run`:

```
zoominfo-exit --dry-run --dump-delete-list --from-id 0 --to-id <int>
              [--whitelist-url ...] [--s3-bucket ...] [...]
```

- Passing `--dump-delete-list` with `--delete-contacts` (or without `--dry-run`) aborts non-zero.
- The run stays read-only and keeps the reader endpoint.
- Runbook: `--whitelist-url` is mandated for dump runs — the file is the direct input to a destructive bulk action, same defense parity as delete runs.

## Artifact

**`delete_candidates_{run_id}.csv`** — one row per **distinct** delete-candidate contact, deduped by sf_id across the whole run (two prospects sharing an email can target the same contact; first prospect encountered wins the traceability columns). Streamed, flushed per batch, joins `report_files` so `--s3-bucket` uploads it.

| Column | Meaning |
|---|---|
| `Id` | Contact sf_id — Salesforce casing, so the file feeds Data Loader's delete operation unchanged (extra columns are ignored by its field mapping) |
| `prospect_id` | First `sp_prospect.id` that flagged the contact |
| `prospect_email` | That prospect's email, for review |

Summary JSON gains `dump_delete_list` (bool) and `distinct_delete_candidates` (deduped — the existing `delete_candidate_contacts` counter is per-prospect-row and is not). Both keys present as false/0 in all modes (stable-schema convention). Stdout table gains a `Distinct (dumped to CSV)` line.

## Sharding (first-class — 2026-08-24, Tomas)

Sharding stays the preferred way to run the dump (overnight full-range runs; a single shard has taken ~13h). `zoominfo-exit-launch --dump-delete-list` forwards the flag to every shard, so each shard uploads its own `delete_candidates_{run_id}.csv`.

The same sf_id can appear in two shards' files (prospects sharing an email across id ranges), and a duplicate Id would land as a spurious error in the admin's Data Loader run. So the handoff file is produced by a **standalone stdlib-only script** (deliberately not an app command — merging is a laptop-side helper needing no server infra, DB, or docker; a drift-guard test pins its header to the writer's):

```
python scripts/merge_delete_candidates.py shard1.csv shard2.csv ... --output delete_candidates_merged.csv
```

It merges in shard order, dedupes by sf_id (first occurrence wins), validates headers, refuses an output path that is also an input, and prints files/rows/distinct/duplicates_dropped. Collect the shard files from `s3://<bucket>/zoominfo-exit/<run_id>/` — each shard has its own run_id ending in the launch date (`{from}-{to}-{YYYYMMDDHHMMSS}`), e.g. `aws s3 cp s3://<bucket>/zoominfo-exit/ . --recursive --exclude '*' --include '*/delete_candidates_*'` then keep the night's files by timestamp.

## Tracking after an admin bulk delete (agreed direction, not built)

An admin bulk delete bypasses uDab, so `sf_contact.deleted_at` / `delete_reason` are not written at delete time (nightly sync will still flip `IsDeleted` via `/queryAll`). Plan: since we have the dumped list (and Data Loader emits a success CSV), a later small ingest command marks the confirmed-deleted rows in `sf_contact`, preserving the provenance record from decision #6 of the delete spec. To be specced when the admin route is confirmed; per repo convention, no placeholder was built.

## Tests

`tests/test_zoominfo_exit_dump_list.py`: flag validation (with delete mode / without any mode), CSV header + rows + empty-email handling, run-wide dedupe across evaluations and batches with first-prospect-wins, header-only file when no candidates, stable summary keys in non-dump runs, stats + file registration flowing into summary/table/uploads, launcher forwarding `--dump-delete-list` to every shard (and omitting it by default), and merge (script loaded by file path): header drift-guard vs the writer, cross-shard dedupe with first-occurrence-wins, header validation, output-is-input refusal, non-zero exit on unreadable input.
