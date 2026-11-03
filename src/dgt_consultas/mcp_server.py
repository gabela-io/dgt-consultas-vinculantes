"""MCP server exposing the corpus index to agents.

Tools return small, bounded payloads: search first, then read only the
sections that matter. Run with `dgt-consultas mcp --corpus <repo root>`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

from .search import Index

Field = Literal["all", "question", "facts", "answer", "legislation"]
Section = Literal["facts", "question", "answer"]

INSTRUCTIONS = """\
Binding rulings (consultas vinculantes) of Spain's Dirección General de
Tributos (DGT), 1997 to date. Content is in Spanish. Only the DGT's binding
doctrine: no TEAC, courts, non-binding consultas, or regional/foral tax
authorities.

Recommended flow:
1. `search` with concrete Spanish terms (Spanish stemming: singular/plural and
   accents don't matter). Use `year_from` for recent doctrine and
   `field="question"` to match what each consulta is about.
2. `read` only promising consultas and only the sections needed (usually
   `question` and `answer`).
3. `by_legislation` for everything citing a law/article; `citations` to see
   which later consultas follow or cite a criterion.
4. `stale` before relying on a consulta: it reports BOE amendments to the
   provisions the answer quotes and later consultas applying new wording.
Every result carries a `status`. "superseded (…) → V…" means a human or the DGT
marked at least part of the ruling as outdated: read the note (`read` returns
it) to see which part, since the core criterion often still holds.
"unreviewed" means nobody has checked it, not that it is current. Always cite by number (V0063-24) and date. Criteria may have changed since a
consulta was issued: prefer the most recent ones.
"""


MAX_STALE = 20  # numbers per stale call: each may fetch BOE laws


def build_server(corpus: Path, db_path: Path | None = None) -> FastMCP:
    index = Index(corpus, db_path)
    mcp = FastMCP("dgt-consultas", instructions=INSTRUCTIONS)
    cache: dict = {}

    @mcp.tool()
    def search(
        query: str,
        year_from: int | None = None,
        year_to: int | None = None,
        office: str | None = None,
        field: Field = "all",
        limit: int = 10,
        match: Literal["all", "any"] = "all",
        offset: int = 0,
    ) -> dict:
        """Search DGT binding consultas; returns number, date, office, cited
        legislation and a ~300-character snippet per result.

        - query: Spanish terms ("modelo 347 arrendamiento"), a quoted phrase
          ("inversión del sujeto pasivo"), OR/NOT, prefix with * (cripto*), or a
          consulta number (V0063-24) to jump straight to it.
        - match "all" requires every term (falls back to "any" with a notice if
          nothing matches); "any" ranks by relevance with any term.
        - year_from/year_to: years. office: substring of the issuing
          subdirectorate ("Consumo" = VAT, "Renta" = personal income tax,
          "Jurídicas" = corporate tax). field: restrict matching to one field.
        """
        return index.search(
            query,
            year_from,
            year_to,
            office,
            field,
            max(1, min(limit, 50)),
            match,
            max(0, offset),
        )

    @mcp.tool()
    def read(
        number: str,
        sections: list[Section] | None = None,
        max_chars: int = 8000,
    ) -> dict:
        """Metadata and the requested sections of one consulta (default: facts,
        question and answer), truncated to max_chars in total."""
        return index.read(number, sections, max_chars)

    @mcp.tool()
    def by_legislation(
        law: str,
        article: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        limit: int = 50,
    ) -> dict:
        """Consultas whose published `normativa` field mentions a law ("37/1992",
        "Ley 35/2006") and optionally an article ("164"), newest first. Text
        match on that field only; laws cited only in the answer body are not
        covered."""
        return index.by_legislation(
            law, article, year_from, year_to, max(1, min(limit, 200))
        )

    @mcp.tool()
    def citations(number: str) -> dict:
        """Consultas this one cites (by V-number) and consultas citing it, with
        dates. Useful to follow a criterion over time."""
        return index.citations(number)

    @mcp.tool()
    def stale(
        numbers: list[str],
        window_days: int = 365,
        since: str | None = None,
        min_confidence: float = 0.0,
    ) -> list[dict]:
        """Check whether consultas may no longer reflect the law in force.

        For each number: status (possibly_stale | no_signals |
        insufficient_data), signals (norm_amended / norm_repealed: which block
        of which law changed, when, by which norm, window before/after the
        consulta, evidence, confidence) and later consultas from the same
        office (similar facts, or using the amendment's new wording).
        Signals are evidence to review, not a legal verdict. `since`
        (YYYY-MM-DD) keeps only changes effective from that date.
        Fetches BOE data on first use (cached for a week). At most 20 numbers
        per call.
        """
        from .boe import BOE
        from .stale import StaleChecker, to_json

        # One BOE client per server: its parsed laws and boilerplate set are
        # reused across calls (the IDF table is loaded per checker).
        if "boe" not in cache:
            cache["boe"] = BOE(index.corpus / "index" / "boe")
        try:
            checker = StaleChecker(
                index, cache["boe"], window_days, since, min_confidence
            )
        except ValueError as exc:
            return [{"error": str(exc)}]
        return [to_json(checker.check(n)) for n in numbers[:MAX_STALE]]

    @mcp.tool()
    def info() -> dict:
        """Index coverage: number of consultas, marker entries and latest date."""
        return index.info()

    return mcp
