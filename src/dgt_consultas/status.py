"""Status of every consulta, kept apart from the official text.

Two independent statuses:

  publication  What the DGT says: published, annulled, replaced (with the
               number of its replacement), unpublished, not_binding, or empty
               (PETETE serves no text). Derived from the data, no judgement.
  currency     Whether the criterion still holds. Only set from evidence with
               a named source: a human review in reviews/<number>.yaml, or the
               DGT itself replacing the ruling or stating, in a later ruling,
               that it changes the criterion. Everything else is
               "unreviewed"; automatic signals from `stale` are separate.

The Markdown files are never edited: the result goes to status/consultas.jsonl.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import frontmatter
import yaml

from .checkpoint import atomic_write_text
from .criteria import Link, links_in
from .index import SECTIONS, base_number, fold, section

NUMBER_IN_TEXT_RE = re.compile(r"\b(V?\d{1,4}-\d{2})\b", re.IGNORECASE)
REVIEW_STATUSES = {"current", "superseded"}


@dataclass
class Currency:
    status: str = "unreviewed"  # current | superseded | unreviewed
    source: str | None = None  # review | dgt_replacement | dgt_explicit
    superseded_by: str | None = None
    reviewed: str | None = None
    by: str | None = None
    note: str | None = None


@dataclass
class Status:
    number: str
    path: str
    publication: str
    replaced_by: str | None = None
    currency: Currency = field(default_factory=Currency)
    clarified_by: list[str] = field(default_factory=list)


def publication_status(stem: str) -> tuple[str, str | None]:
    """(publication status, replacement number) from a file name."""
    text = fold(stem)
    if "reemplazada" in text:
        numbers = NUMBER_IN_TEXT_RE.findall(stem)
        return "replaced", numbers[-1] if len(numbers) > 1 else None
    if "anulad" in text:
        return "annulled", None
    if "no es objeto de publicacion" in text:
        return "unpublished", None
    if "no vinculante" in text:
        return "not_binding", None
    if "sin contenido" in text:
        return "empty", None
    return "published", None


def load_reviews(corpus: Path) -> dict[str, dict]:
    """reviews/<number>.yaml, validated; keyed by number."""
    reviews: dict[str, dict] = {}
    for path in sorted((corpus / "reviews").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected a mapping of fields")  # noqa: TRY004 - a data error, reported like the others
        number = str(data.get("number") or "")
        if number != path.stem:
            raise ValueError(f"{path}: 'number' must match the file name")
        if data.get("status") not in REVIEW_STATUSES:
            raise ValueError(
                f"{path}: 'status' must be one of {sorted(REVIEW_STATUSES)}"
            )
        for key in ("reviewed", "note"):
            if not data.get(key):
                raise ValueError(f"{path}: '{key}' is required")
        reviews[number] = data
    return reviews


def build(corpus: Path) -> list[Status]:
    reviews = load_reviews(corpus)
    out: list[Status] = []
    links: list[Link] = []
    for path in sorted((corpus / "consultas").rglob("*.md")):
        post = frontmatter.load(path)
        number = str(post.metadata.get("numero") or path.stem)
        publication, replaced_by = publication_status(path.stem)
        if publication == "published" and not any(
            section(post.content, heading) for heading in SECTIONS.values()
        ):
            publication = "empty"
        status = Status(number, str(path.relative_to(corpus)), publication, replaced_by)
        if publication == "published":
            links.extend(
                links_in(number, str(post.metadata.get("fecha") or ""), post.content)
            )
        if replaced_by:
            status.currency = Currency("superseded", "dgt_replacement", replaced_by)
        review = reviews.pop(number, None) or reviews.pop(path.stem, None)
        if review:
            status.currency = Currency(
                review["status"],
                "review",
                review.get("superseded_by"),
                str(review["reviewed"]),
                review.get("by"),
                " ".join(str(review["note"]).split()),
            )
        out.append(status)
    if reviews:
        raise ValueError(f"reviews for unknown consultas: {', '.join(sorted(reviews))}")
    known = {base_number(s.number) for s in out}
    for s in out:
        target = s.currency.superseded_by
        if (
            s.currency.source == "review"
            and target
            and base_number(target) not in known
        ):
            raise ValueError(
                f"reviews/{s.number}.yaml: superseded_by {target} is not in the corpus"
            )
    _apply_links(out, links)
    return out


def _apply_links(statuses: list[Status], links: list[Link]) -> None:
    """DGT statements: a stated change of criterion supersedes the earlier
    consulta (unless a review or a replacement already decided); a
    clarification is recorded without touching currency."""
    by_number: dict[str, Status] = {}
    for s in statuses:
        by_number.setdefault(s.number.upper(), s)

    def find(number: str) -> Status | None:
        number = number.upper()
        alt = number[1:] if number.startswith("V") else "V" + number
        return by_number.get(number) or by_number.get(alt)

    for link in sorted(links, key=lambda l: (l.date, l.source)):
        target = find(link.target)
        if target is None:
            continue
        if link.kind == "clarifies":
            if link.source not in target.clarified_by:
                target.clarified_by.append(link.source)
        elif target.currency.source is None:  # earliest stated change wins
            target.currency = Currency(
                "superseded",
                "dgt_explicit",
                link.source,
                link.date,
                note=link.sentence
                if len(link.sentence) <= 600
                else link.sentence[:599] + "…",
            )


def is_default(s: Status) -> bool:
    return (
        s.publication == "published"
        and s.currency.status == "unreviewed"
        and not s.clarified_by
    )


def write(corpus: Path, statuses: list[Status]) -> Path:
    """status/consultas.jsonl, stable order.

    Only consultas that differ from the default (published, unreviewed) are
    written; a number missing from the file has the default status.
    """
    path = corpus / "status" / "consultas.jsonl"
    path.parent.mkdir(exist_ok=True)
    lines = [
        json.dumps(asdict(s), ensure_ascii=False, sort_keys=True)
        for s in statuses
        if not is_default(s)
    ]
    content = "\n".join(lines) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != content:
        atomic_write_text(path, content)
    return path


def load(corpus: Path) -> dict[str, dict]:
    """status/consultas.jsonl keyed by number (case-insensitive lookups: upper)."""
    path = corpus / "status" / "consultas.jsonl"
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        out[row["number"].upper()] = row
        # Markers are stored under their full name; also reachable by number.
        out.setdefault(base_number(row["number"]), row)
    return out


def label(row: dict | None) -> str:
    """One-line status for listings: 'superseded (review 2026-10-19) → V1435-24'."""
    if not row:
        return "unreviewed"  # consultas missing from the file have the default status
    cur = row["currency"]
    if row["publication"] not in ("published",) and not row.get("replaced_by"):
        return row["publication"]
    clarified = row.get("clarified_by") or []
    tail = f" · clarified by {', '.join(clarified)}" if clarified else ""
    if cur["status"] == "unreviewed":
        return "unreviewed" + tail
    source = {
        "review": f"review {cur['reviewed']}",
        "dgt_explicit": f"DGT change of criterion {cur['reviewed']}",
    }.get(cur["source"], "DGT")
    text = f"{cur['status']} ({source})"
    if cur.get("superseded_by"):
        text += f" → {cur['superseded_by']}"
    return text + tail
