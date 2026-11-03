"""Atom feeds of the latest consultas: one for all, one per issuing office.

Written to a directory served by GitHub Pages (docs/feeds/), so anyone can
subscribe in a feed reader and see new rulings without searching. Output is
deterministic: regenerating with no new consultas changes nothing.
"""

from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import frontmatter

from .checkpoint import atomic_write_text
from .index import MARKER_RE, SECTIONS, fold, section

ATOM = "http://www.w3.org/2005/Atom"
ALL_LIMIT = 100
OFFICE_LIMIT = 50
YEARS_SCANNED = 3  # the latest entries always live in the newest year directories
REPO_URL = "https://github.com/gabela-io/dgt-consultas-vinculantes"
SUMMARY_CHARS = 280


@dataclass
class Entry:
    number: str
    date: str
    office: str
    legislation: str
    question: str
    url: str
    path: str


# Issuing offices grouped into feeds people recognise. Matching is on the
# folded office name, so typos ("G de Impuestos…") and renames ("SG de
# Operaciones Financieras" → "SG de Tributación de las Operaciones
# Financieras") land in the same feed.
OFFICE_FEEDS = [
    ("renta de las personas fisicas", "irpf", "IRPF"),
    ("consumo", "iva", "IVA"),
    ("personas juridicas", "sociedades", "Impuesto sobre Sociedades"),
    (
        "patrimoniales",
        "patrimoniales",
        "Impuestos patrimoniales, tasas y precios públicos",
    ),
    (
        "especiales",
        "especiales",
        "Impuestos especiales, comercio exterior y medio ambiente",
    ),
    ("fiscalidad internacional", "internacional", "Fiscalidad internacional"),
    ("operaciones financieras", "operaciones-financieras", "Operaciones financieras"),
    ("tributos locales", "tributos-locales", "Tributos locales"),
]


def office_feed(office: str) -> tuple[str, str]:
    """(feed slug, label) for an issuing office."""
    text = fold(office)
    for needle, feed_slug, label in OFFICE_FEEDS:
        if needle in text:
            return feed_slug, label
    rest = re.sub(r"^\s*(s?g|subdireccion general) de ", "", text.strip())
    return re.sub(r"[^a-z0-9]+", "-", rest).strip("-") or "otros", office.strip()


def slug(office: str) -> str:
    return office_feed(office)[0]


def latest_entries(corpus: Path) -> list[Entry]:
    years = sorted(
        (p for p in (corpus / "consultas").iterdir() if p.is_dir()), reverse=True
    )
    entries = []
    for year_dir in years[:YEARS_SCANNED]:
        for path in year_dir.glob("*.md"):
            if MARKER_RE.search(path.stem):
                continue
            post = frontmatter.load(path)
            fm = post.metadata
            if not fm.get("fecha"):
                continue
            entries.append(
                Entry(
                    number=str(fm.get("numero") or path.stem),
                    date=str(fm["fecha"]),
                    office=str(fm.get("organo") or ""),
                    legislation=str(fm.get("normativa") or ""),
                    question=" ".join(
                        section(post.content, SECTIONS["question"]).split()
                    ),
                    url=str(fm.get("url") or ""),
                    path=str(path.relative_to(corpus)),
                )
            )
    entries.sort(key=lambda e: (e.date, e.number), reverse=True)
    return entries


def _el(
    parent: ET.Element, tag: str, text: str | None = None, **attrs: str
) -> ET.Element:
    el = ET.SubElement(parent, f"{{{ATOM}}}{tag}", attrs)
    if text is not None:
        el.text = text
    return el


def build_feed(
    entries: list[Entry], feed_slug: str, title: str, base_url: str
) -> bytes:
    ET.register_namespace("", ATOM)
    feed = ET.Element(f"{{{ATOM}}}feed")
    _el(feed, "id", f"{base_url}/feeds/{feed_slug}.xml")
    _el(feed, "title", title)
    _el(
        feed,
        "subtitle",
        "Consultas vinculantes de la Dirección General de Tributos. Origen de los datos: Ministerio de Hacienda.",
    )
    _el(
        feed,
        "updated",
        f"{entries[0].date}T00:00:00Z" if entries else "1997-01-01T00:00:00Z",
    )
    _el(feed, "link", rel="self", href=f"{base_url}/feeds/{feed_slug}.xml")
    _el(feed, "link", rel="alternate", href=REPO_URL)
    author = _el(feed, "author")
    _el(author, "name", "gabela-io (unofficial)")
    for e in entries:
        entry = _el(feed, "entry")
        _el(entry, "id", e.url or f"{REPO_URL}/blob/main/{e.path}")
        question = e.question if len(e.question) <= 120 else e.question[:119] + "…"
        _el(entry, "title", f"{e.number} · {question}" if question else e.number)
        _el(entry, "updated", f"{e.date}T00:00:00Z")
        if e.url:
            _el(entry, "link", rel="alternate", href=e.url)
        _el(entry, "link", rel="related", href=f"{REPO_URL}/blob/main/{e.path}")
        _el(entry, "category", term=slug(e.office), label=e.office)
        summary = (
            e.question
            if len(e.question) <= SUMMARY_CHARS
            else e.question[: SUMMARY_CHARS - 1] + "…"
        )
        _el(
            entry,
            "summary",
            f"{e.office}. Normativa: {e.legislation}. {summary}".strip(),
        )
    ET.indent(feed)
    return (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        + ET.tostring(feed, encoding="utf-8")
        + b"\n"
    )


def _index_html(feeds: list[tuple[str, str, int]], base_url: str, updated: str) -> str:
    items = "\n".join(
        f'    <li><a href="feeds/{s}.xml">{html.escape(t)}</a> ({n} entries)</li>'
        for s, t, n in feeds
    )
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Consultas vinculantes de la DGT · feeds</title>
</head>
<body>
  <h1>Consultas vinculantes de la DGT</h1>
  <p>Atom feeds of the latest binding rulings, updated weekly. Subscribe to one in any feed reader.</p>
  <ul>
{items}
  </ul>
  <p>Last update: {html.escape(updated)}.</p>
  <p>Unofficial project, not endorsed by the Ministerio de Hacienda. Origen de los datos: Ministerio de Hacienda.
  Source and data: <a href="{REPO_URL}">{REPO_URL}</a>.</p>
</body>
</html>
"""


def write_feeds(
    corpus: Path, out_dir: Path, base_url: str
) -> list[tuple[str, str, int]]:
    """Write all.xml, one feed per office and an index.html one level up."""
    base_url = base_url.rstrip("/")
    entries = latest_entries(corpus)
    out_dir.mkdir(parents=True, exist_ok=True)
    feeds = [("all", "Consultas vinculantes DGT: todas", entries[:ALL_LIMIT])]
    by_feed: dict[str, tuple[str, list[Entry]]] = {}
    for e in entries:
        if e.office:
            feed_slug, label = office_feed(e.office)
            by_feed.setdefault(feed_slug, (label, []))[1].append(e)
    for feed_slug, (label, items) in sorted(by_feed.items()):
        feeds.append(
            (feed_slug, f"Consultas vinculantes DGT: {label}", items[:OFFICE_LIMIT])
        )

    written = set()
    for feed_slug, title, items in feeds:
        path = out_dir / f"{feed_slug}.xml"
        data = build_feed(items, feed_slug, title, base_url)
        if not path.exists() or path.read_bytes() != data:
            atomic_write_text(path, data.decode("utf-8"))
        written.add(path.name)
    for stale in out_dir.glob("*.xml"):
        if stale.name not in written:
            stale.unlink()

    summary = [(s, t, len(i)) for s, t, i in feeds]
    index = out_dir.parent / "index.html"
    # The reuse terms require stating when the data was last updated.
    # The last fetch run, not the newest ruling: PETETE publishes with delay.
    updated = max((e.date for e in entries), default="unknown")
    meta = corpus / "metadata.json"
    if meta.exists():
        updated = str(
            json.loads(meta.read_text(encoding="utf-8")).get("last_run") or updated
        )[:10]
    content = _index_html(summary, base_url, updated)
    if not index.exists() or index.read_text(encoding="utf-8") != content:
        atomic_write_text(index, content)
    (out_dir.parent / ".nojekyll").touch()
    return summary
