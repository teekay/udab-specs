---
kind: spec
status: in-progress
area: zoominfo-exit
updated: 2026-09-04
repos: [udab-server]
summary: "Archived 5x5 persons count as a defense: matcher archive pass, 2 reason codes, indexes. Built 2026-09-04, unmerged."
---

# ZoomInfo Exit — Archived 5x5 Persons Count as a Defense

**Status: built 2026-09-04 (Tomas), branch `zoominfo-5x5-archive-incl`, unmerged.**

## Background

The Aug 26 delete-candidate list had ~19K more SF contacts than the Aug 7 run because the 5x5 vendor reload of Aug 11–14 archived ~24.6M persons not confirmed in 180 days (and overwrote emails on others). See `NOTES.md` → "Why the Aug 26 delete list had ~20K more contacts". Client decision: **once a contact was received from 5x5 it counts, archived or not.**

## Change

1. **Matcher** (`app/commands/zoominfo_exit/matcher.py`): after the live `5x5_universal_person` pass, emails still unmatched get a second pass against `5x5_universal_person_archive`. Two statements, not a UNION (collation mismatch between the tables); the archive pass only sees live-unmatched emails, so live always wins. Within each pass business beats personal, unchanged.
2. **Reason codes** (`codes.py`, KEEP): `FIVE_X_FIVE_ARCHIVE_BUSINESS_EMAIL`, `FIVE_X_FIVE_ARCHIVE_PERSONAL_EMAIL`. The decisions CSV's `five_x_five_column` carries the new matcher column values (`archive_business_email` / `archive_personal_emails`) verbatim. Summary JSON gains the two counters via `ALL_REASON_CODES` (stable-schema convention holds).
3. **Migration `7e94c1a5b2d8`**: `ix_5x5_up_archive_business_email` + `ix_5x5_up_archive_personal_emails` on the archive table, `ALGORITHM=INPLACE, LOCK=NONE` (~24.6M rows — hours of background IO, online).
4. **Reporting**: `zoominfo-exit/merge_summaries.py` (workspace) gained descriptions for both codes; unlisted codes group as KEEP defenses automatically.

## Explicitly not covered

Contacts whose 5x5 person is still live but whose email the reload overwrote (2,867 on the Aug 26 list) — no 5x5 row carries the old email anymore (verified 2026-09-07: of their 2,154 distinct emails, 0 hit the live table and only 9 appear on other archived persons, so the archive pass rescues a mere handful of these). A durable "seen in 5x5" record (e.g. stamped on `sp_prospect` at first match) would close this; **not built** pending client confirmation that they care about this remainder.

## Runbook

1. Merge + deploy; CI applies the migration. **Wait for the index build to finish before launching shards** (check `SHOW INDEX FROM 5x5_universal_person_archive`).
2. Rerun the full dump: `zoominfo-exit-launch --dump-delete-list ...` exactly as in `dump-delete-list.md`, then the merge script.
3. Expect the new list ≈ Aug 26 minus ~15K (archive-defended). The two new reason-code lines in the summary quantify it.

## Tests

`test_zoominfo_exit_matcher.py`: archived business/personal match, business-beats-personal within archive, live-beats-archive, live-personal-beats-archive-business (two-pass rule). `test_zoominfo_exit_engine.py`: both new columns map to their reason codes; archive match still short-circuits SF evaluation. All 122 zoominfo-exit tests pass (2026-09-04).
