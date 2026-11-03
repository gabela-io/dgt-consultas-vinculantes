"""SQLite index over the Markdown corpus, built for fast, compact lookups.

The index stores metadata per consulta plus a contentless FTS5 table over
Spanish-stemmed text (Snowball), so "criptomoneda" and "criptomonedas" match
the same documents and accents don't matter. Original text is never copied
into the database: snippets and sections are read from the Markdown files,
which keeps the index small and the corpus the single source of truth.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import frontmatter
import snowballstemmer

# Section key -> Markdown heading as written by the exporter.
SECTIONS = {
    "facts": "Descripcion de hechos",
    "question": "Cuestion planteada",
    "answer": "Contestacion",
}
FTS_COLUMNS = ("question", "facts", "answer", "legislation")
# bm25() weights, in FTS_COLUMNS order: the question is the best summary of
# what a consulta is about; the answer is long and quotes a lot of law.
BM25_WEIGHTS = (4.0, 2.0, 1.0, 2.0)

MARKER_RE = re.compile(
    r"anulad|sin contenido|reemplazada|no es objeto de publicaci|no vinculante",
    re.IGNORECASE,
)
# Cited consulta numbers; PETETE writes a few in lowercase (v1735-25).
CITATION_RE = re.compile(r"\bV\d{4}-\d{2}\b", re.IGNORECASE)
BASE_NUMBER_RE = re.compile(r"^\s*(V?\d{1,4}-\d{2})", re.IGNORECASE)


def base_number(number: str) -> str:
    """Lookup key for a consulta: its leading number, uppercased.

    'V0007-03 Consulta sin contenido al ser reemplazada…' -> 'V0007-03',
    'v1649-19' -> 'V1649-19', '0027-06' -> '0027-06' (no "V" is a different
    consulta, so it is not added).
    """
    m = BASE_NUMBER_RE.match(number)
    return (m.group(1) if m else number).strip().upper()


WORD_RE = re.compile(r"\w+", re.UNICODE)

# Spanish function words: dropped from queries so "any term" searches don't
# match the whole corpus. Documents keep them (phrase queries still work).
STOPWORDS = frozenset(
    [
        "a",
        "al",
        "algo",
        "algun",
        "alguna",
        "algunas",
        "alguno",
        "algunos",
        "ante",
        "antes",
        "aquel",
        "aquella",
        "aquellas",
        "aquellos",
        "aqui",
        "asi",
        "cada",
        "como",
        "con",
        "contra",
        "cual",
        "cuales",
        "cuando",
        "de",
        "del",
        "desde",
        "donde",
        "dos",
        "el",
        "ella",
        "ellas",
        "ello",
        "ellos",
        "en",
        "entre",
        "era",
        "es",
        "esa",
        "esas",
        "ese",
        "eso",
        "esos",
        "esta",
        "estas",
        "este",
        "esto",
        "estos",
        "fue",
        "fueron",
        "ha",
        "han",
        "hasta",
        "hay",
        "la",
        "las",
        "le",
        "les",
        "lo",
        "los",
        "mas",
        "me",
        "mi",
        "mis",
        "muy",
        "no",
        "nos",
        "o",
        "os",
        "otra",
        "otras",
        "otro",
        "otros",
        "para",
        "pero",
        "poco",
        "por",
        "porque",
        "que",
        "quien",
        "se",
        "sea",
        "segun",
        "ser",
        "si",
        "sido",
        "sin",
        "sobre",
        "son",
        "su",
        "sus",
        "tambien",
        "tan",
        "te",
        "tiene",
        "tienen",
        "todo",
        "todos",
        "tu",
        "tus",
        "un",
        "una",
        "unas",
        "uno",
        "unos",
        "y",
        "ya",
    ]
)

_stemmer = snowballstemmer.stemmer("spanish")


def fold(text: str) -> str:
    """Lowercase and strip accents."""
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


@lru_cache(maxsize=500_000)
def stem(word: str) -> str:
    return _stemmer.stemWord(word)


def stem_text(text: str) -> str:
    return " ".join(stem(w) for w in WORD_RE.findall(fold(text)))


def section(body: str, title: str) -> str:
    m = re.search(
        rf"^## {re.escape(title)}\s*\n(.*?)(?=^## |\Z)", body, re.DOTALL | re.MULTILINE
    )
    return m.group(1).strip() if m else ""


@dataclass
class Record:
    number: str
    year: int
    date: str
    office: str
    legislation: str
    marker: bool
    path: str
    facts: str
    question: str
    answer: str


def iter_records(corpus: Path) -> Iterator[Record]:
    for path in sorted((corpus / "consultas").rglob("*.md")):
        post = frontmatter.load(path)
        fm = post.metadata
        year = path.parent.name
        yield Record(
            number=str(fm.get("numero") or path.stem),
            year=int(year) if year.isdigit() else 0,
            date=str(fm.get("fecha") or ""),
            office=str(fm.get("organo") or ""),
            legislation=str(fm.get("normativa") or ""),
            marker=bool(MARKER_RE.search(path.stem)),
            path=str(path.relative_to(corpus)),
            **{k: section(post.content, t) for k, t in SECTIONS.items()},
        )


SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE consulta (
    id INTEGER PRIMARY KEY,
    number TEXT NOT NULL,
    base TEXT NOT NULL,
    year INTEGER NOT NULL,
    date TEXT NOT NULL,
    office TEXT NOT NULL,
    legislation TEXT NOT NULL,
    legislation_fold TEXT NOT NULL,
    marker INTEGER NOT NULL,
    path TEXT NOT NULL
);
CREATE INDEX consulta_base ON consulta (base);
CREATE INDEX consulta_year ON consulta (year);
CREATE TABLE citation (source TEXT NOT NULL, target TEXT NOT NULL, PRIMARY KEY (source, target));
CREATE INDEX citation_target ON citation (target);
-- Document frequency of stems over facts + question (for stale's IDF).
CREATE TABLE df (term TEXT PRIMARY KEY, df INTEGER NOT NULL) WITHOUT ROWID;
CREATE VIRTUAL TABLE consulta_fts USING fts5(
    question, facts, answer, legislation,
    content='', tokenize='unicode61 remove_diacritics 2'
);
"""


def build(corpus: Path, db_path: Path) -> int:
    """(Re)build the index at db_path. Returns the number of records indexed."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    n = 0
    df: Counter = Counter()
    with con:
        for i, r in enumerate(iter_records(corpus), start=1):
            if not r.marker:
                df.update(set(stem_text(r.facts + " " + r.question).split()))
            con.execute(
                "INSERT INTO consulta VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    i,
                    r.number,
                    base_number(r.number),
                    r.year,
                    r.date,
                    r.office,
                    r.legislation,
                    fold(r.legislation),
                    int(r.marker),
                    r.path,
                ),
            )
            con.execute(
                "INSERT INTO consulta_fts (rowid, question, facts, answer, legislation)"
                " VALUES (?,?,?,?,?)",
                (
                    i,
                    stem_text(r.question),
                    stem_text(r.facts),
                    stem_text(r.answer),
                    stem_text(r.legislation),
                ),
            )
            # Markers cite nothing; one lists ~3,300 skipped 2026 numbers.
            source = base_number(r.number)
            targets = (
                set()
                if r.marker
                else {t.upper() for t in CITATION_RE.findall(r.answer)} - {source}
            )
            for target in targets:
                con.execute(
                    "INSERT OR IGNORE INTO citation VALUES (?,?)", (source, target)
                )
            n = i
        con.executemany("INSERT INTO df VALUES (?,?)", df.items())
        con.execute("INSERT INTO meta VALUES ('records', ?)", (str(n),))
    con.execute("INSERT INTO consulta_fts (consulta_fts) VALUES ('optimize')")
    con.commit()
    con.execute("VACUUM")
    con.close()
    tmp.replace(db_path)
    return n
