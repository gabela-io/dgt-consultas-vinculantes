"""Query side of the index: compact results meant for agents and the CLI.

Every function returns small, bounded payloads (metadata + a short snippet)
so a caller can scan dozens of consultas without reading whole files, and
then fetch only the sections it needs with `read`.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path

import frontmatter

from . import status as status_mod
from .index import (
    BM25_WEIGHTS,
    FTS_COLUMNS,
    SECTIONS,
    STOPWORDS,
    WORD_RE,
    base_number,
    fold,
    section,
    stem,
)

NUMBER_RE = re.compile(r"^\s*(V?)\s*(\d{1,4})\s*[-/]\s*(\d{2})\s*$", re.IGNORECASE)
TOKEN_RE = re.compile(r'"[^"]*"|\S+')
OPERATORS = {"OR", "AND", "NOT"}
SNIPPET_CHARS = 300
MAX_LIMIT = 200  # results per call; callers (MCP) may cap lower
MIN_PREFIX = 3  # "a*" would match the whole corpus
DATE_IN_TEXT_RE = re.compile(
    r"\bde \d{1,2} de (?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|"
    r"setiembre|octubre|noviembre|diciembre)\b"
)
PETETE_URL = "https://petete.tributos.hacienda.gob.es/consultas/?num_consulta={}"


@dataclass
class Hit:
    number: str
    date: str
    office: str
    legislation: str
    snippet: str = ""
    path: str = field(default="", repr=False)
    status: str = ""


def normalize_number(text: str) -> str | None:
    """'v63-24' -> 'V0063-24', '27/06' -> '0027-06'; None if it isn't a number.

    The "V" prefix is kept as typed: pre-2006 numbering has no prefix and
    "0027-06" and "V0027-06" are different consultas. Lookups fall back to
    the other form when the typed one doesn't exist.
    """
    m = NUMBER_RE.match(text)
    if not m:
        return None
    prefix = "V" if m.group(1) else ""
    return f"{prefix}{int(m.group(2)):04d}-{m.group(3)}"


class Index:
    def __init__(self, corpus: Path, db_path: Path | None = None) -> None:
        self.corpus = corpus
        self.db_path = db_path or corpus / "index" / "dgt.sqlite"
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"No index at {self.db_path}; build it with `dgt-consultas index`"
            )
        self.con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        self._statuses: dict[str, dict] | None = None
        self._statuses_mtime: float | None = None
        self.con.row_factory = sqlite3.Row

    # -- helpers ---------------------------------------------------------

    def info(self) -> dict:
        row = self.con.execute(
            "SELECT count(*) AS n, sum(marker) AS markers, max(date) AS latest FROM consulta"
        ).fetchone()
        return {
            "consultas": row["n"] - row["markers"],
            "markers": row["markers"],
            "latest_date": row["latest"],
            "index": str(self.db_path),
        }

    def _row(self, number: str) -> sqlite3.Row | None:
        """Look a consulta up by number: case-insensitive, markers by their
        leading number, and falling back between "V0027-06" and "0027-06"
        (a few rulings were published without the "V")."""
        key = base_number(number)
        alt = key[1:] if key.startswith("V") else "V" + key
        for n in (key, alt):
            row = self.con.execute(
                "SELECT * FROM consulta WHERE base = ? ORDER BY marker LIMIT 1", (n,)
            ).fetchone()
            if row:
                return row
        return None

    def _sections(self, path: str) -> dict[str, str]:
        body = frontmatter.load(self.corpus / path).content
        return {k: section(body, t) for k, t in SECTIONS.items()}

    def _status(self, number: str) -> dict | None:
        # Reload when the file changes: a long-running MCP server must see
        # reviews added after it started.
        path = self.corpus / "status" / "consultas.jsonl"
        mtime = path.stat().st_mtime if path.exists() else None
        if self._statuses is None or mtime != self._statuses_mtime:
            self._statuses = status_mod.load(self.corpus)
            self._statuses_mtime = mtime
        return self._statuses.get(number.upper()) or self._statuses.get(
            base_number(number)
        )

    def _hit(self, row: sqlite3.Row, snippet: str = "") -> Hit:
        legislation = row["legislation"]
        if len(legislation) > 160:
            legislation = legislation[:157] + "…"
        return Hit(
            row["number"],
            row["date"],
            row["office"],
            legislation,
            snippet,
            row["path"],
            status_mod.label(self._status(row["number"])),
        )

    # -- query building --------------------------------------------------

    @staticmethod
    def fts_query(text: str, match: str = "all") -> tuple[str, set[str], int]:
        """Translate a user query into an FTS5 expression over stemmed text.

        Words are folded and stemmed like the index. Quoted text becomes a
        phrase; OR/AND/NOT between terms pass through; `word*` is a prefix
        query on the word's stem (at least 3 letters). Stray operators (leading,
        trailing, doubled, or left alone when a stopword is dropped) are
        removed, so the expression is always valid. Returns the expression, the
        stems used (for snippets) and the number of terms.
        """
        parts: list[str] = []
        stems: set[str] = set()
        for tok in TOKEN_RE.findall(text):
            if tok in OPERATORS:
                parts.append(tok)
                continue
            if tok.startswith('"') and tok.endswith('"') and len(tok) > 1:
                words = [stem(w) for w in WORD_RE.findall(fold(tok[1:-1]))]
                if words:
                    stems.update(words)
                    parts.append('"' + " ".join(words) + '"')
                continue
            if tok.endswith("*") and len(tok) > 1:
                words = WORD_RE.findall(fold(tok[:-1]))
                if words and len(words[-1]) >= MIN_PREFIX:
                    # The index holds stems ("criptomonedas" -> "criptomoned"),
                    # so the prefix must be stemmed too.
                    ws = [stem(w) for w in words]
                    stems.update(ws)
                    parts.append('"' + " ".join(ws) + '" *')
                continue
            words = [w for w in WORD_RE.findall(fold(tok)) if w not in STOPWORDS]
            if not words:
                continue
            ws = [stem(w) for w in words]
            stems.update(ws)
            parts.append('"' + " ".join(ws) + '"')

        # Operators only between two terms: drop leading/trailing ones, keep
        # the last of a run. A "NOT" with nothing before it can't be expressed.
        clean: list[str] = []
        for p in parts:
            if p in OPERATORS:
                if clean and clean[-1] in OPERATORS:
                    clean[-1] = p
                elif clean:
                    clean.append(p)
            else:
                clean.append(p)
        while clean and clean[-1] in OPERATORS:
            clean.pop()
        terms = sum(p not in OPERATORS for p in clean)
        if not clean:
            return "", stems, 0
        joiner = " OR " if match == "any" else " "
        expr = ""
        for i, p in enumerate(clean):
            if i and p not in OPERATORS and clean[i - 1] not in OPERATORS:
                expr += joiner
            elif i:
                expr += " "
            expr += p
        return expr, stems, terms

    def _snippet(self, path: str, stems: set[str], order: list[str]) -> str:
        secs = self._sections(path)
        best, best_score = "", -1
        for name in order:
            for para in secs.get(name, "").split("\n\n"):
                found = {stem(w) for w in WORD_RE.findall(fold(para))} & stems
                if len(found) > best_score:
                    best, best_score = para, len(found)
            if best_score == len(stems) and stems:
                break
        if not best:
            return ""
        start = 0
        for m in WORD_RE.finditer(best):
            if stem(fold(m.group())) in stems:
                start = max(0, m.start() - 80)
                break
        text = " ".join(best[start : start + SNIPPET_CHARS].split())
        prefix = "…" if start else ""
        suffix = "…" if start + SNIPPET_CHARS < len(best) else ""
        return prefix + text + suffix

    # -- public API ------------------------------------------------------

    def search(
        self,
        query: str,
        year_from: int | None = None,
        year_to: int | None = None,
        office: str | None = None,
        field: str = "all",
        limit: int = 10,
        match: str = "all",
        offset: int = 0,
    ) -> dict:
        """Ranked search. Returns {"match", "total", "results": [Hit…], "notice"?}.

        A query that is just a consulta number goes straight to it. With
        match="all" and no hits, retries with match="any" and says so.
        """
        limit = max(1, min(limit, MAX_LIMIT))
        offset = max(0, offset)
        number = normalize_number(query)
        if number:
            row = self._row(number)
            if row:
                return {
                    "match": "number",
                    "total": 1,
                    "results": [asdict(self._hit(row, "(lookup by number)"))],
                }
            # Not a known number ("37/92"): search it as text instead.

        expr, stems, terms = self.fts_query(query, match)
        if not expr:
            return {"match": match, "total": 0, "results": [], "notice": "empty query"}
        if field != "all":
            if field not in FTS_COLUMNS:
                raise ValueError(f"field must be one of: all, {', '.join(FTS_COLUMNS)}")
            expr = f"{{{field}}} : ({expr})"

        where = ["consulta_fts MATCH ?", "c.marker = 0"]
        params: list = [expr]
        if year_from:
            where.append("c.year >= ?")
            params.append(year_from)
        if year_to:
            where.append("c.year <= ?")
            params.append(year_to)
        if office:
            where.append("c.office LIKE ?")
            params.append(f"%{office}%")
        weights = ", ".join(str(w) for w in BM25_WEIGHTS)
        sql = (
            f"SELECT c.*, bm25(consulta_fts, {weights}) AS rank FROM consulta_fts "
            f"JOIN consulta c ON c.id = consulta_fts.rowid WHERE {' AND '.join(where)}"
        )
        try:
            total = self.con.execute(
                f"SELECT count(*) FROM ({sql})", params
            ).fetchone()[0]
        except sqlite3.OperationalError as exc:
            return {
                "match": match,
                "total": 0,
                "results": [],
                "error": f"invalid query: {exc}",
            }
        if total == 0 and match == "all" and terms > 1:
            fallback = self.search(
                query, year_from, year_to, office, field, limit, "any", offset
            )
            fallback["notice"] = (
                "no results with all terms; showing results with any term"
            )
            return fallback

        rows = self.con.execute(
            f"{sql} ORDER BY rank LIMIT ? OFFSET ?", [*params, limit, offset]
        ).fetchall()
        order = [field] if field in SECTIONS else ["question", "facts", "answer"]
        return {
            "match": match,
            "total": total,
            "results": [
                asdict(self._hit(r, self._snippet(r["path"], stems, order)))
                for r in rows
            ],
        }

    def read(
        self,
        number: str,
        sections: list[str] | None = None,
        max_chars: int = 8000,
    ) -> dict:
        """Metadata plus the requested sections, truncated to max_chars in total."""
        number = normalize_number(number) or number
        row = self._row(number)
        if row is None:
            return {"error": f"no consulta {number}"}
        wanted = sections or ["facts", "question", "answer"]
        secs = self._sections(row["path"])
        out = {
            "number": row["number"],
            "date": row["date"],
            "office": row["office"],
            "legislation": row["legislation"],
            "url": PETETE_URL.format(row["base"]),
            "marker": bool(row["marker"]),
            "status": self._status(row["number"]),
        }
        budget = max(0, max_chars)
        for name in wanted:
            if name not in SECTIONS:
                raise ValueError(f"unknown section: {name}")
            text = secs[name]
            if len(text) > budget:
                text = (
                    text[:budget] + f"… [truncated: {len(secs[name])} chars in total]"
                )
            out[name] = text
            budget = max(0, budget - len(text))
        return out

    def by_legislation(
        self,
        law: str,
        article: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        limit: int = 50,
    ) -> dict:
        """Consultas whose `normativa` field mentions a law (and an article).

        Plain text matching on the field as PETETE publishes it, not a parsed
        reference: "37/1992" matches "Ley 37/1992 art. 20", and article "20"
        matches "20" as a whole number in that field, ignoring the law's own
        number, dates ("de 28 de diciembre") and ordinals ("20-Uno-28º").
        """
        law_fold = fold(law).strip()
        if len(law_fold) < 3:
            raise ValueError("law must have at least 3 characters (e.g. '37/1992')")
        escaped = law_fold.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where = ["marker = 0", "legislation_fold LIKE ? ESCAPE '\\'"]
        params: list = [f"%{escaped}%"]
        limit = max(1, min(limit, MAX_LIMIT))
        if year_from:
            where.append("year >= ?")
            params.append(year_from)
        if year_to:
            where.append("year <= ?")
            params.append(year_to)
        rows = self.con.execute(
            f"SELECT * FROM consulta WHERE {' AND '.join(where)} ORDER BY date DESC",
            params,
        ).fetchall()
        if article:
            art = re.compile(rf"(?<![\d.]){re.escape(fold(article))}(?![\dºª°o])")

            def mentions_article(text: str) -> bool:
                text = text.replace(law_fold, " ")
                text = DATE_IN_TEXT_RE.sub(" ", text)
                return bool(art.search(text))

            rows = [r for r in rows if mentions_article(r["legislation_fold"])]
        return {
            "total": len(rows),
            "results": [asdict(self._hit(r)) for r in rows[:limit]],
        }

    def citations(self, number: str) -> dict:
        """Consultas this one cites, and consultas citing it (by V-number)."""
        number = base_number(normalize_number(number) or number)

        def collect(sql: str) -> list[dict]:
            out = []
            for (n,) in self.con.execute(sql, (number,)):
                r = self._row(n)
                out.append(
                    {
                        "number": n,
                        "date": r["date"] if r else None,
                        "exists": r is not None,
                    }
                )
            return sorted(out, key=lambda x: x["date"] or "")

        return {
            "number": number,
            "cites": collect("SELECT target FROM citation WHERE source = ?"),
            "cited_by": collect("SELECT source FROM citation WHERE target = ?"),
        }
