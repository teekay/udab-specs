---
kind: howto
status: done
area: infra
updated: 2026-10-13
repos: [udab-server]
summary: "Recipe to make a local dev DB match PROD charsets/collations per column, with loss guard, backup and FK handling."
---

# Local dev DB: align charsets and collations with PROD

## Symptom

Hub list calls answer 500 and the server log shows

```
ERROR 1267 (HY000): Illegal mix of collations (utf8mb4_0900_ai_ci,IMPLICIT)
and (utf8mb4_general_ci,IMPLICIT) for operation '='
```

Cause: the local DB was created with tables defaulting to `utf8mb4_general_ci`
(the `sf_*` mirrors were loaded that way), while tables created later by
migrations take the server default `utf8mb4_0900_ai_ci`. PROD is different
again: nearly everything is `latin1_swedish_ci`, with a few tables on
`utf8mb4_0900_ai_ci`. MySQL compares two columns of the **same charset but
different collations** by refusing outright (the error above). Two columns of
**different charsets** are fine: the narrower one is converted, and the index on
the wider side still works. So "close enough" collations break and PROD's
mixed state works. The fix is to match PROD exactly, column by column.

## What the recipe does

One script, three steps, run on the host (not in a container):

1. **snapshot**: reads every column's type, charset, collation and the table
   default from PROD's `information_schema` over the VPN (read-only) into a TSV.
2. **plan**: diffs the local DB against the snapshot for the chosen tables
   (default: the 19 Hub tables), adds the foreign-key columns in *other* tables
   that point at them (MySQL refuses a parent column whose charset differs from
   its children's), then runs a **data-loss scan**: every column whose charset
   would narrow is round-tripped through the target charset and compared
   byte-for-byte. If any row would lose a character it prints the columns and
   **stops with exit code 2** without writing DDL. Otherwise it writes
   `align.sql`: one `ALTER TABLE` per table with the new table default and a
   `MODIFY` per column that carries the column's current nullability, default,
   extra and comment unchanged.
3. **apply**: `mysqldump`s the touched tables (gzipped) into the work dir, runs
   `align.sql` in one session with `foreign_key_checks=0` and
   `STRICT_ALL_TABLES` (a bad conversion aborts instead of truncating), then
   re-runs the diff on the touched tables and expects zero differences.

Same-charset collation changes and widenings are lossless by construction;
the scan only guards charset narrowing (in practice utf8mb4 to latin1).

## Prerequisites

- `aiqvpn` working, PROD reader password at hand (line under `tom.kohl` in
  `secrets.txt`; strip whitespace).
- A `mysql` client on the host for the PROD query; `docker` for the local DB
  (container `udab-db`, root/root, schema `udab`; override with
  `LOCAL_DB_CONTAINER`, `LOCAL_DB_NAME`, `LOCAL_DB_USER`, `LOCAL_DB_PASSWORD`).
- The local app stack can keep running; the ALTERs rebuild tables with the
  COPY algorithm and took ~20 s for 458k `sf_task` rows on CANARO.

## Steps

```bash
cd ~/projects/sapper-consulting
# 1. PROD snapshot (read-only)
aiqvpn up
export MYSQL_PWD=$(sed -n '16p' secrets.txt | tr -d '[:space:]')   # check the line number
python3 udab-server/scripts/local/align_local_collation.py snapshot
unset MYSQL_PWD; aiqvpn down

# 2. Plan for the Hub tables (default list); read the summary
python3 udab-server/scripts/local/align_local_collation.py plan
#    --tables a,b,c  to pick tables;  --all  for every table on both sides
#    exit code 2 + "STOP" = a column holds characters PROD's charset cannot
#    store; nothing was written. Decide per column before going on.

# 3. Apply (dumps the touched tables first, then re-diffs)
python3 udab-server/scripts/local/align_local_collation.py apply
```

Work dir: `/tmp/align_local_collation` (`ALIGN_WORKDIR` to change). It holds
`prod_cols.tsv`, `align.sql` (review it before `apply` if you like),
`touched_tables.json` and `backup_<stamp>.sql.gz`.

Afterwards, the enrich seed's two align steps (`align_scorecard_collation`,
`align_task_call_id` in `scripts/local/seed_appointment_calls_enrich.py`)
become no-ops, because the drift they paper over is gone.

## Rollback

Restore the dump: `zcat backup_<stamp>.sql.gz | docker exec -i udab-db mysql
-uroot -proot udab`. The dump carries full `CREATE TABLE` statements, so it
brings back the old charsets as well as the rows. Converting latin1 back to
utf8mb4 by hand is lossless too.

## Pitfalls met on CANARO (2026-10-12)

- **Loss scan must compare bytes.** `col <> CONVERT(CONVERT(col USING latin1)
  USING utf8mb4)` itself throws the illegal-mix error, because the inner
  `CONVERT` comes back in the server collation. Wrap both sides in
  `CAST(... AS BINARY)`.
- **Foreign keys.** 16 tables outside the Hub set (`sp_account`,
  `sp_talk_track_session`, `sp_dne`, ...) hold `varchar(18)` keys into
  `sf_account`, `sf_contact`, `sf_lead`, `sf_user`. Change parent and children
  in the same session with `foreign_key_checks=0`; the script does. PROD has no
  FK on `sp_dne.account_id` and keeps it `utf8mb4_unicode_ci`; locally that
  column follows the parent (latin1) so the local FK stays valid.
- **Primary-key columns.** A `MODIFY` must restate `NOT NULL` for a PK part
  (`ERROR 1171`); the script reads nullability from `information_schema`
  rather than assuming `NULL`.
- **Comments.** Pull column details with `--default-character-set=utf8mb4`,
  or an em-dash in a comment comes out as a latin1 byte and is rewritten
  wrong.
- **`--all` on this machine stops** on `sf_qualified_appointment_sheet`
  (139 rows with characters outside latin1). That is the guard working;
  those tables were left alone.

## Result on CANARO

19 Hub tables, 268 columns and 18 table defaults converted, 19 FK columns in
other tables, zero rows lost, row counts unchanged, all 20 Hub page queries
and the Salesmanship ones at the same timings as before. A re-run of `plan`
reports zero differences.

## The script

Lives untracked at `udab-server/scripts/local/align_local_collation.py` on
CANARO. Full copy for another machine:

```python
#!/usr/bin/env python3
"""Make the local dev DB's charsets and collations match PROD, column by column.

Runs on the host (needs python3, docker, and the mysql client for PROD).

  python3 align_local_collation.py snapshot   # PROD column definitions -> prod_cols.tsv (aiqvpn up first)
  python3 align_local_collation.py plan       # diff local vs snapshot, scan for data loss, write align.sql
  python3 align_local_collation.py apply      # dump the touched tables, run align.sql, re-diff

Defaults cover the Hub tables; --tables a,b,c overrides, --all takes every table that exists on both
sides. Columns in other tables that hold a foreign key into a selected table are converted too, because
MySQL refuses a parent column whose charset differs from its children's.
"""
import argparse
import csv
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys

HUB_TABLES = [
    "sf_account", "sf_contact", "sf_lead", "sf_quality_scorecard", "sf_task", "sf_user",
    "sp_appointment_email", "sp_appointment_email_image", "sp_call_adherence", "sp_call_adherence_node",
    "sp_call_insight", "sp_call_insight_item", "sp_call_transcript", "sp_call_transcript_feedback",
    "sp_call_transcript_highlight", "sp_setting", "sp_transcription_job", "sp_transcription_job_task", "sp_user",
]
WORK = pathlib.Path(os.environ.get("ALIGN_WORKDIR", "/tmp/align_local_collation"))
LOCAL_CONTAINER = os.environ.get("LOCAL_DB_CONTAINER", "udab-db")
LOCAL_DB = os.environ.get("LOCAL_DB_NAME", "udab")
LOCAL_USER, LOCAL_PASSWORD = os.environ.get("LOCAL_DB_USER", "root"), os.environ.get("LOCAL_DB_PASSWORD", "root")
PROD_HOST = os.environ.get("PROD_DB_HOST", "ecs-apps-prod-encrypted.cluster-ro-c7bc84q7nhla.us-east-1.rds.amazonaws.com")
PROD_USER, PROD_DB = os.environ.get("PROD_DB_USER", "tom.kohl"), os.environ.get("PROD_DB_NAME", "sapper")

COLUMNS_SQL = """
SELECT c.table_name, c.column_name, c.column_type, IFNULL(c.character_set_name, ''), IFNULL(c.collation_name, ''),
       t.table_collation, c.is_nullable, IFNULL(c.column_default, '<<NULL>>'), c.extra, c.column_comment
FROM information_schema.columns c
JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE c.table_schema = DATABASE() AND t.table_type = 'BASE TABLE'
ORDER BY c.table_name, c.ordinal_position"""
FK_SQL = """
SELECT k.table_name, k.column_name, k.referenced_table_name, k.referenced_column_name
FROM information_schema.key_column_usage k
JOIN information_schema.columns c ON c.table_schema = k.table_schema AND c.table_name = k.table_name AND c.column_name = k.column_name
WHERE k.table_schema = DATABASE() AND k.referenced_table_name IS NOT NULL AND c.character_set_name IS NOT NULL"""


def local_sql(sql, raw=False):
    cmd = f"mysql -u{LOCAL_USER} -p{LOCAL_PASSWORD} --default-character-set=utf8mb4 {'' if raw else '-N -B '}{LOCAL_DB}"
    run = subprocess.run(["docker", "exec", "-i", LOCAL_CONTAINER, "sh", "-c", cmd], input=sql, capture_output=True, text=True)
    err = run.stderr.replace("mysql: [Warning] Using a password on the command line interface can be insecure.", "").strip()
    if run.returncode:
        raise SystemExit(f"local mysql failed: {err}")
    return run.stdout


def rows(tsv):
    return list(csv.reader(tsv.splitlines(), delimiter="\t"))


def by_column(tsv):
    return {(r[0], r[1]): r[2:] for r in rows(tsv)}


def snapshot():
    if "MYSQL_PWD" not in os.environ:
        raise SystemExit("export MYSQL_PWD=<prod password> first (never on the command line)")
    WORK.mkdir(parents=True, exist_ok=True)
    out = subprocess.run(["mysql", "-h", PROD_HOST, "-u", PROD_USER, PROD_DB, "--connect-timeout=20", "-N", "-B", "-e", COLUMNS_SQL],
                         capture_output=True, text=True)
    if out.returncode:
        raise SystemExit(f"prod query failed (VPN up?): {out.stderr.strip()}")
    (WORK / "prod_cols.tsv").write_text(out.stdout)
    print(f"snapshot: {len(rows(out.stdout))} PROD columns -> {WORK / 'prod_cols.tsv'}")


def esc(s):
    return s.replace("\\", "\\\\").replace("'", "\\'")


def modify_clause(column, ctype, charset, collation, nullable, default, extra, comment):
    parts = [f"MODIFY `{column}` {ctype} CHARACTER SET {charset} COLLATE {collation}", "NULL" if nullable == "YES" else "NOT NULL"]
    if default != "<<NULL>>":
        parts.append(f"DEFAULT '{esc(default)}'")
    if extra and "GENERATED" not in extra.upper():
        parts.append(extra)
    if comment:
        parts.append(f"COMMENT '{esc(comment)}'")
    return " ".join(parts)


def plan(args):
    prod = by_column((WORK / "prod_cols.tsv").read_text())
    local = by_column(local_sql(COLUMNS_SQL))
    both = sorted({k[0] for k in prod} & {k[0] for k in local})
    tables = both if args.all else (args.tables.split(",") if args.tables else HUB_TABLES)
    missing = [t for t in tables if t not in both]
    if missing:
        print("skipped (not on both sides):", ", ".join(missing))
    tables = [t for t in tables if t in both]

    changes = {}        # table -> list of (column, type, charset, collation)
    table_default = {}  # table -> PROD table collation
    for (t, c), p in prod.items():
        if t not in tables or (t, c) not in local:
            continue
        l = local[(t, c)]
        if p[3] != l[3]:
            table_default[t] = p[3]
        if p[2] and (p[2] != l[2] or p[0] != l[0]):
            changes.setdefault(t, []).append((c, p[0], p[1], p[2]))

    # Foreign keys from other tables into the selected ones must follow the parent's charset.
    fk_changes = {}
    for child_t, child_c, parent_t, parent_c in rows(local_sql(FK_SQL)):
        if parent_t not in tables or child_t in tables or (child_t, child_c) not in local:
            continue
        parent = prod.get((parent_t, parent_c)) or local[(parent_t, parent_c)]
        target = prod.get((child_t, child_c))
        if not target or target[1] != parent[1]:  # PROD has no FK there or a different charset: follow the parent
            target = [local[(child_t, child_c)][0], parent[1], parent[2]]
        if target[2] != local[(child_t, child_c)][2]:
            fk_changes.setdefault(child_t, []).append((child_c, target[0], target[1], target[2]))

    print(f"tables with differences: {len(set(changes) | set(table_default))}; columns to change: "
          f"{sum(map(len, changes.values()))}; foreign-key columns elsewhere: {sum(map(len, fk_changes.values()))}")

    # Data-loss scan: any charset change is checked by a binary round trip through the target charset.
    loss = {}
    for t, cols in {**changes, **fk_changes}.items():
        narrowing = [(c, cs) for c, _, cs, _ in cols if local[(t, c)][1] and local[(t, c)][1] != cs]
        if not narrowing:
            continue
        exprs = ", ".join(f"SUM(`{c}` IS NOT NULL AND CAST(`{c}` AS BINARY) <> "
                          f"CAST(CONVERT(CONVERT(`{c}` USING {cs}) USING {local[(t, c)][1]}) AS BINARY))" for c, cs in narrowing)
        counts = [int(v) if v != "NULL" else 0 for v in local_sql(f"SELECT {exprs} FROM `{t}`").split()]
        bad = {c: n for (c, _), n in zip(narrowing, counts) if n}
        if bad:
            loss[t] = bad
    if loss:
        print("STOP: these columns hold characters the PROD charset cannot represent; converting would lose data:")
        for t, bad in loss.items():
            print(f"  {t}: {bad}")
        (WORK / "align.sql").unlink(missing_ok=True)
        sys.exit(2)
    print("data-loss scan: no row loses a character")

    stmts = []
    for t in sorted(set(changes) | set(table_default)):
        clauses = []
        if t in table_default:
            clauses.append(f"DEFAULT CHARACTER SET {table_default[t].split('_')[0]} COLLATE {table_default[t]}")
        clauses += [modify_clause(c, ctype, cs, coll, *local[(t, c)][4:]) for c, ctype, cs, coll in changes.get(t, [])]
        stmts.append(f"ALTER TABLE `{t}`\n  " + ",\n  ".join(clauses) + ";")
    for t, cols in sorted(fk_changes.items()):
        stmts.append(f"ALTER TABLE `{t}`\n  " + ",\n  ".join(modify_clause(c, ctype, cs, coll, *local[(t, c)][4:]) for c, ctype, cs, coll in cols) + ";")
    sql = ("SET SESSION foreign_key_checks=0;\nSET SESSION sql_mode=CONCAT(@@sql_mode, ',STRICT_ALL_TABLES');\n"
           + "\n".join(stmts) + "\nSET SESSION foreign_key_checks=1;\n")
    (WORK / "align.sql").write_text(sql)
    (WORK / "touched_tables.json").write_text(json.dumps(sorted(set(changes) | set(table_default) | set(fk_changes))))
    print(f"{len(stmts)} ALTER statements -> {WORK / 'align.sql'}")
    if not stmts:
        print("nothing to do: local already matches PROD for these tables")


def apply():
    sql_path = WORK / "align.sql"
    if not sql_path.exists():
        raise SystemExit("run `plan` first (and make sure it did not stop on data loss)")
    touched = json.loads((WORK / "touched_tables.json").read_text())
    if not touched:
        print("nothing to apply"); return
    backup = WORK / f"backup_{dt.datetime.now():%Y%m%d_%H%M}.sql.gz"
    dump = (f"mysqldump -u{LOCAL_USER} -p{LOCAL_PASSWORD} --single-transaction --quick --default-character-set=utf8mb4 "
            f"{LOCAL_DB} {' '.join(touched)} | gzip")
    with open(backup, "wb") as fh:
        run = subprocess.run(["docker", "exec", LOCAL_CONTAINER, "sh", "-c", dump + " 2>/dev/null"], stdout=fh)
    if run.returncode:
        raise SystemExit("backup failed; nothing applied")
    print(f"backup of {len(touched)} tables -> {backup} ({backup.stat().st_size // 1_000_000} MB)")
    started = dt.datetime.now()
    local_sql(sql_path.read_text(), raw=True)
    print(f"applied in {(dt.datetime.now() - started).seconds}s; re-checking ...")
    args = argparse.Namespace(all=False, tables=",".join(touched))
    plan(args)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["snapshot", "plan", "apply"])
    ap.add_argument("--tables", help="comma-separated table list (default: the Hub tables)")
    ap.add_argument("--all", action="store_true", help="every table present on both sides")
    args = ap.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    {"snapshot": snapshot, "plan": lambda: plan(args), "apply": apply}[args.action]()


if __name__ == "__main__":
    main()
```
