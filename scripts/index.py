#!/usr/bin/env python3
"""Generate udab-specs/README.md from the frontmatter of every spec file.

Run from anywhere:  python3 scripts/index.py   (or: uv run --no-project scripts/index.py)
Stdlib only; the frontmatter is parsed by hand, so no PyYAML is needed.
Exits non-zero with a clear message on a missing/invalid frontmatter.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

KINDS = ("spec", "howto", "notes", "handoff")
STATUSES = ("draft", "ready", "in-progress", "done", "superseded", "dropped")
ACTIVE = ("in-progress", "ready", "draft")
ARCHIVED = ("superseded", "dropped")
REQUIRED = ("kind", "status", "area", "updated", "repos", "summary")
SUMMARY_MAX = 120

# Display order and titles; unknown areas are appended alphabetically.
AREA_TITLES = {
    "transcription": "Transcription",
    "appointment-emails": "Appointment emails",
    "zoominfo-exit": "ZoomInfo exit",
    "extension": "Extension",
    "talk-track": "Talk track",
    "dnc": "DNC",
    "infra": "Infra",
}

HEADER = """# uDab specs

Specs, how-tos, research notes and handoff reports for the uDab platform, one folder per area. Every file carries frontmatter: `kind` (spec | howto | notes | handoff), `status` (draft = analysis, not approved; ready = approved, not built; in-progress = partially built; done = built and merged; superseded = a later doc changed the approach, see `superseded_by`; dropped = never built, abandoned), `area`, `updated` (date of the last status change), `repos`, `summary`.
This file is generated — do not edit by hand; run `python3 scripts/index.py` from `udab-specs/` after changing any frontmatter.
Reading rule: open the area's `NOTES.md` first, then only active specs (draft/ready/in-progress) for your area; don't open done/superseded docs unless doing archaeology. Areas without a `NOTES.md` gain one when their first living reference is needed.
"""


class IndexError_(Exception):
    pass


def fail(msg: str) -> None:
    print(f"index.py: {msg}", file=sys.stderr)
    sys.exit(1)


def parse_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise IndexError_("missing frontmatter (file must start with '---')")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise IndexError_("unterminated frontmatter (no closing '---')")
    meta: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise IndexError_(f"bad frontmatter line: {line!r}")
        key, value = line.split(":", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        meta[key.strip()] = value
    return meta


def validate(path: Path, meta: dict[str, str]) -> None:
    missing = [k for k in REQUIRED if k not in meta or not meta[k]]
    if missing:
        raise IndexError_(f"missing frontmatter field(s): {', '.join(missing)}")
    if meta["kind"] not in KINDS:
        raise IndexError_(f"unknown kind {meta['kind']!r} (expected one of {', '.join(KINDS)})")
    if meta["status"] not in STATUSES:
        raise IndexError_(f"unknown status {meta['status']!r} (expected one of {', '.join(STATUSES)})")
    if meta["status"] == "superseded" and not meta.get("superseded_by"):
        raise IndexError_("status is 'superseded' but 'superseded_by' is missing")
    if meta.get("superseded_by") and not (ROOT / meta["superseded_by"]).is_file():
        raise IndexError_(f"superseded_by points at a missing file: {meta['superseded_by']}")
    area_dir = path.parent.relative_to(ROOT).as_posix()
    if meta["area"] != area_dir:
        raise IndexError_(f"area {meta['area']!r} does not match directory {area_dir!r}")
    try:
        date.fromisoformat(meta["updated"])
    except ValueError:
        raise IndexError_(f"updated {meta['updated']!r} is not a YYYY-MM-DD date")
    if len(meta["summary"]) > SUMMARY_MAX:
        raise IndexError_(f"summary is {len(meta['summary'])} chars (max {SUMMARY_MAX})")


def collect() -> tuple[dict[str, list[dict]], dict[str, Path]]:
    docs: dict[str, list[dict]] = {}
    notes: dict[str, Path] = {}
    for path in sorted(ROOT.rglob("*.md")):
        rel = path.relative_to(ROOT)
        if rel.parts[0] in (".git", "scripts") or path == README:
            continue
        if path.parent == ROOT:
            fail(f"{rel}: spec files must live in an area directory, not at the top level")
        area = rel.parts[0]
        if path.name == "NOTES.md":
            notes[area] = path
            continue
        try:
            meta = parse_frontmatter(path)
            validate(path, meta)
        except IndexError_ as exc:
            fail(f"{rel}: {exc}")
        meta["_rel"] = rel.as_posix()
        meta["_name"] = path.name
        docs.setdefault(area, []).append(meta)
    return docs, notes


def table(rows: list[dict]) -> list[str]:
    out = ["| doc | kind | status | updated | summary |", "|---|---|---|---|---|"]
    for m in rows:
        summary = m["summary"].replace("|", "\\|")
        out.append(f"| [{m['_name']}]({m['_rel']}) | {m['kind']} | {m['status']} | {m['updated']} | {summary} |")
    return out


def render(docs: dict[str, list[dict]], notes: dict[str, Path]) -> str:
    areas = [a for a in AREA_TITLES if a in docs or a in notes]
    areas += sorted(a for a in set(docs) | set(notes) if a not in AREA_TITLES)
    lines = [HEADER]
    for area in areas:
        lines.append(f"## {AREA_TITLES.get(area, area)}")
        lines.append("")
        if area in notes:
            lines.append(f"- **Living reference:** [{area}/NOTES.md]({area}/NOTES.md) — read this first.")
            lines.append("")
        rows = docs.get(area, [])
        by_updated = lambda m: (m["updated"], m["_name"])  # noqa: E731
        active = sorted((m for m in rows if m["status"] in ACTIVE), key=by_updated, reverse=True)
        done = sorted((m for m in rows if m["status"] == "done"), key=by_updated, reverse=True)
        archived = sorted((m for m in rows if m["status"] in ARCHIVED), key=by_updated, reverse=True)
        if active:
            lines.append("**Active**")
            lines.append("")
            lines += table(active)
            lines.append("")
        if done:
            lines.append("**Done**")
            lines.append("")
            lines += table(done)
            lines.append("")
        if archived:
            lines.append("<details>")
            lines.append("<summary>Superseded / dropped</summary>")
            lines.append("")
            for m in archived:
                tail = f" — superseded by [{m['superseded_by']}]({m['superseded_by']})" if m.get("superseded_by") else ""
                lines.append(f"- [{m['_name']}]({m['_rel']}) ({m['kind']}, {m['status']}, {m['updated']}){tail}: {m['summary']}")
            lines.append("")
            lines.append("</details>")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    docs, notes = collect()
    if not docs:
        fail("no spec files found")
    README.write_text(render(docs, notes), encoding="utf-8")
    count = sum(len(v) for v in docs.values())
    print(f"wrote {README.relative_to(ROOT)}: {count} docs in {len(docs)} areas, {len(notes)} NOTES.md")


if __name__ == "__main__":
    main()
