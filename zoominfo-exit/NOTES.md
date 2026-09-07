---
kind: notes
status: done
area: zoominfo-exit
updated: 2026-09-04
repos: [udab-server]
summary: "Living reference: 5x5 data lifecycle, why delete lists drift between runs, archive-as-defense decision."
---

# ZoomInfo exit — living reference

Living doc. Update it when a decision or gotcha lands; the specs in this folder are history.

## The 5x5 data lifecycle (verified 2026-09-04)

The 2026-07-14 assumption "`5x5_universal_person_archive` is empty on prod — ignore entirely" (archive.md, question A2) is **retired**. What actually happens:

- Monthly vendor drops are **upserts** (`confirm_5x5_universal_person.py`): existing persons are overwritten in place with the vendor's current values and get `last_confirmed_date = now`; unknown persons are inserted; persons found in the archive are revived. Nothing is ever wiped.
- Since the reload automation (2026-07-26), each completed drop launches `archive-5x5-universal-data`, which moves every person not confirmed for **180 days** into `5x5_universal_person_archive` and deletes it from the live table.
- The first full cycle was version 3.11.0 (reload id 3, ran 2026-08-11 → 08-14). It archived ~24.6M persons (last confirmed ≤ 2026-02-15 = 180 days before the job). Prod sizes 2026-09-04: live 262M rows / 805GB, archive 24.6M / 63GB.

## Why the Aug 26 delete list had ~20K more contacts than the Aug 7 run

Client question answered 2026-09-04; full per-contact evidence in `zoominfo-exit/report/evidence_5x5_lost_defense.csv` (workspace, not this repo).

Of 19,164 contacts new on the Aug 26 list vs Aug 7:

| Cause | Contacts |
|---|---:|
| 5x5 person archived by the 180-day job; prospect email still on the archived row | 15,009 |
| 5x5 person still live, but the 3.11.0 reload overwrote its email columns | 2,867 |
| Contact created in SF after Aug 7 (re-listed from the internal DB, ZoomInfo ID copied along; Primary List Source "Internal Salesforce") | ~1,053 |
| Contact modified in SF after Aug 7 | ~235 |

5,962 contacts also *dropped off* the list between runs (SF activity fired a keep rule). Consequence: **the delete list is a moving target** — every monthly drop + archive cycle shifts it both ways.

## Decision 2026-09-04 (client): archived 5x5 persons still defend

"Once we receive the contact from 5x5, it's ours." Built as the archive-defense change (see `archive-defense.md`): the matcher consults `5x5_universal_person_archive` for emails the live table missed, with two new reason codes. **Known gap**: the 2,867 email-overwrite contacts are not covered — the old email is on no 5x5 row, live or archived (verified 2026-09-07: 0 live hits, 9 of 2,154 emails on other archived persons). Raised with the client via the drift table above; a durable "seen in 5x5" record is the only complete fix and is deliberately not built.

## Operational gotchas

- The live and archive tables have different collations — a `UNION` across them errors ("illegal mix of collations"). The matcher runs two statements.
- The archive table only had a PRIMARY KEY until migration `7e94c1a5b2d8` added the two email indexes; without them archive lookups full-scan 24.6M rows.
- Reader-endpoint queries on these tables are slow; batch probes by `up_id` (PK), not by email, when investigating.
