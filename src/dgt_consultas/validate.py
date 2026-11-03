"""Corpus validation: catch scraper regressions before they reach the corpus.

Every problem is an `Issue(code, path)`. Known problems that come from PETETE
itself (empty rulings, odd numbering) live in a baseline file; validation fails
only on issues that are not in it, so CI flags new breakage without demanding
that upstream data be perfect.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import frontmatter

from .index import MARKER_RE, SECTIONS, section
from .parser import Consulta

REQUIRED_FIELDS = ("numero", "fecha", "organo", "normativa", "url")
NUMBER_RE = re.compile(
    r"^V?\d{1,4}-\d{2}$", re.IGNORECASE
)  # lowercase "v" exists upstream
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True, order=True)
class Issue:
    code: str
    path: str

    def __str__(self) -> str:
        return f"{self.code} {self.path}"


def check_file(path: Path, corpus: Path, today: str) -> Iterator[Issue]:
    rel = str(path.relative_to(corpus))
    post = frontmatter.load(path)
    fm = post.metadata
    marker = bool(MARKER_RE.search(path.stem))

    for key in REQUIRED_FIELDS:
        if key not in fm:
            yield Issue(f"missing_field:{key}", rel)
    number = str(fm.get("numero") or "")
    date = str(fm.get("fecha") or "")

    if not marker:
        if not NUMBER_RE.match(number):
            yield Issue("bad_number", rel)
        elif number != path.stem:
            yield Issue("filename_mismatch", rel)

    if not DATE_RE.match(date):
        yield Issue("bad_date", rel)
    else:
        try:
            dt.date.fromisoformat(date)
        except ValueError:
            yield Issue("bad_date", rel)
        else:
            if date > today:
                yield Issue("future_date", rel)
            petete_date = f"{date[8:10]}/{date[5:7]}/{date[:4]}"
            blank = dict.fromkeys(
                ("office", "legislation", "facts", "question", "answer"), ""
            )
            year = Consulta(number=number or path.stem, date=petete_date, **blank).year
            if year != path.parent.name:
                yield Issue("wrong_year_dir", rel)

    for heading in SECTIONS.values():
        if not re.search(rf"^## {re.escape(heading)}\s*$", post.content, re.MULTILINE):
            yield Issue("missing_section", rel)
            break
    if not marker:
        secs = {k: section(post.content, t) for k, t in SECTIONS.items()}
        if not any(secs.values()):
            yield Issue("empty_consulta", rel)
        elif not secs["answer"]:
            yield Issue("empty_answer", rel)


def validate(corpus: Path) -> list[Issue]:
    today = dt.datetime.now(dt.UTC).date().isoformat()
    issues: list[Issue] = []
    for path in sorted((corpus / "consultas").rglob("*.md")):
        issues.extend(check_file(path, corpus, today))
    return sorted(issues)


def load_baseline(path: Path) -> set[Issue]:
    if not path.exists():
        return set()
    entries = json.loads(path.read_text(encoding="utf-8"))
    return {Issue(e["code"], e["path"]) for e in entries}


def write_baseline(path: Path, issues: list[Issue]) -> None:
    entries = [{"code": i.code, "path": i.path} for i in sorted(issues)]
    path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
