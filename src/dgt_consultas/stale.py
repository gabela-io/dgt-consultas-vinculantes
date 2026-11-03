"""Signals that a consulta may no longer reflect the law or doctrine in force.

For each consulta:

  norm_amended   A norm the consulta relies on (cited in `normativa` or in the
                 answer) has a block version that took effect inside the
                 window (from `window_days` before the consulta until today),
                 and the changed text overlaps with the consulta's facts and
                 question. Overlap is scored with corpus IDF, so rare shared
                 terms ("guionistas", "escritor") count and boilerplate doesn't.
  norm_repealed  A norm it relies on was repealed after the consulta.

Later consultas from the same office with similar facts/question are listed
as context. Signals are evidence, never a legal verdict: whether a change
actually alters the answer is for a human (or a bounded model) to judge.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher

import frontmatter

from .boe import BOE, Block
from .index import SECTIONS, STOPWORDS, WORD_RE, fold, section, stem
from .search import Index, normalize_number

DEFAULT_WINDOW_DAYS = 365
MIN_TERM_IDF = 2.0  # ignore stems present in >~13% of consultas
# A changed term is "rare" when it appears in the facts/question of <~0.25% of
# consultas ("escritor", "guionista"). Only rare terms count as overlap:
# summing common legal vocabulary ("corresponde", "derecho") flags everything.
RARE_IDF = 6.0
# Rare changed terms shared with the facts/question make a "conclusion" signal
# when their IDF adds up to this. One shared term alone must be very specific
# (in <~0.1% of consultas): "escritor" (7.9) passes, "editorial" (6.4) doesn't.
CONCLUSION_SCORE = 7.0
SIMILARITY_THRESHOLD = 0.25  # IDF-weighted Jaccard on facts + question
QUESTION_SIMILARITY = (
    0.17  # ...and on the question alone: same issue, not just same vocabulary
)
# The DGT quotes the provisions it applies verbatim. A block counts as relied
# upon only if the answer shares a run of SHINGLE words with the block's text
# as it read on the consulta's date; this filters out incidental overlaps.
SHINGLE = 6
# "Quoted text rewritten" needs a substantial change, not a one-word edit.
# A block is "applied" only if the answer quotes at least this many
# distinctive word runs of it (boilerplate runs shared by 3+ blocks excluded).
MIN_DISTINCTIVE_QUOTE = 2
# Severities that make a consulta possibly_stale. "text_changed" (the quoted
# passage was edited, but not on the facts' terms or only in numbers) and
# "repealed" are reported for review but don't change the status.
STALE_SEVERITIES = {"conclusion", "reference_or_amount"}
SEVERITY_ORDER = {
    "conclusion": 0,
    "reference_or_amount": 1,
    "text_changed": 2,
    "repealed": 3,
}
MAX_LATER = 5

# Official citation forms, as the DGT writes them in answers and `normativa`.
# Identifier patterns, like V-numbers: they don't interpret the text.
RANK_FORMS = [
    (
        r"Real Decreto Legislativo|R\.?D\.?\s?Leg(?:islativo)?\.?|RDLeg|RDLG",
        "Real Decreto Legislativo",
    ),
    (r"Real Decreto-ley|Real Decreto Ley|R\.?D\.?-?L\.?|RDL", "Real Decreto-ley"),
    (r"Real Decreto|R\.?D\.?", "Real Decreto"),
    (r"Ley Orgánica|L\.?O\.?", "Ley Orgánica"),
    (r"Ley", "Ley"),
]
CITATION_RE = re.compile(
    r"\b(?P<rank>"
    + "|".join(p for p, _ in RANK_FORMS)
    + r")\s*(?P<num>\d{1,4}/(?:19|20)\d{2})\b"
)
ORDER_RE = re.compile(r"\bOrden\s+(?P<num>[A-Z]{2,4}/\d{1,5}/(?:19|20)\d{2})\b")

# Acronyms used in `normativa`. Value: (rank, number) or a list of
# (valid_from YYYYMMDD, rank, number) when the acronym changed meaning.
ACRONYMS: dict[str, tuple[str, str] | list[tuple[str, str, str]]] = {
    "LIRPF": ("Ley", "35/2006"),
    "RIRPF": ("Real Decreto", "439/2007"),
    "LIVA": ("Ley", "37/1992"),
    "RIVA": ("Real Decreto", "1624/1992"),
    "LGT": ("Ley", "58/2003"),
    "TRLRHL": ("Real Decreto Legislativo", "2/2004"),
    "TRLITPAJD": ("Real Decreto Legislativo", "1/1993"),
    "LISD": ("Ley", "29/1987"),
    "TRLIRNR": ("Real Decreto Legislativo", "5/2004"),
    "TRLIS": ("Real Decreto Legislativo", "4/2004"),
    "LIS": [
        ("20150101", "Ley", "27/2014"),
        ("00000000", "Real Decreto Legislativo", "4/2004"),
    ],
}
ACRONYM_RE = re.compile(
    r"\b(" + "|".join(sorted(ACRONYMS, key=len, reverse=True)) + r")\b"
)


@dataclass
class Signal:
    kind: str
    law: str
    boe_id: str
    effective: str
    window: str
    evidence: str
    confidence: float
    block: str | None = None
    amended_by: str | None = None
    severity: str = "conclusion"
    shared_terms: list[str] = field(default_factory=list, repr=False)
    added_terms: list[str] = field(default_factory=list, repr=False)


@dataclass
class Result:
    number: str
    date: str
    checked_at: str
    status: str
    signals: list[Signal] = field(default_factory=list)
    later_consultas: list[dict] = field(default_factory=list)
    norms: list[str] = field(default_factory=list)
    error: str | None = None


def _iso(yyyymmdd: str) -> str:
    return (
        f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
        if len(yyyymmdd) == 8
        else yyyymmdd
    )


def _terms(text: str) -> Counter:
    """Stem counts of alphabetic, non-stopword tokens."""
    return Counter(
        stem(w)
        for w in WORD_RE.findall(fold(text))
        if w.isalpha() and w not in STOPWORDS and len(w) > 2
    )


def shingles(text: str, n: int = SHINGLE) -> set[tuple[str, ...]]:
    """Runs of n consecutive folded words."""
    words = WORD_RE.findall(fold(text))
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


UNIT_AFTER = {"ciento", "euros", "euro", "meses", "dias", "anos"}
UNIT_BEFORE = {"casilla", "casillas"}
PASSAGE_CONTEXT = 30  # words around an edit that define "the edited passage"
PASSAGE_MIN_IDF = 3.0  # topical terms linking an edited passage to the question


def amounts(words: list[str], start: int, end: int) -> set[str]:
    """Numbers in words[start:end] that are amounts: followed by a unit ("25 por
    ciento", "300.000 euros") or preceded by "casilla" ("casilla 61")."""
    out = set()
    for i in range(max(0, start), min(len(words), end)):
        w = words[i]
        if not w.isdigit():
            continue
        after = words[i + 1 : i + 4]
        before = words[max(0, i - 2) : i]
        if UNIT_AFTER & set(after) or UNIT_BEFORE & set(before):
            out.add(w)
    return out


def norm_references(legislation: str, answer: str, date: str) -> list[tuple[str, str]]:
    """(rank, number) of norms named in the `normativa` field or the answer."""
    refs: list[tuple[str, str]] = []
    compact = date.replace("-", "")
    for text in (legislation, answer):
        for m in CITATION_RE.finditer(text):
            raw = m.group("rank")
            rank = next(r for p, r in RANK_FORMS if re.fullmatch(p, raw))
            refs.append((rank, m.group("num")))
        for m in ORDER_RE.finditer(text):
            refs.append(("Orden", m.group("num")))
    for m in ACRONYM_RE.finditer(legislation):
        value = ACRONYMS[m.group(1)]
        if isinstance(value, list):
            value = next((r, n) for since, r, n in value if compact >= since)
        refs.append(value)
    return list(dict.fromkeys(refs))


def parse_since(since: str | None) -> str | None:
    """'2024-01-01' or '20240101' -> '2024-01-01' (the format of
    Signal.effective); anything else is an error rather than a silent
    string comparison."""
    if not since:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return dt.datetime.strptime(since, fmt).date().isoformat()  # noqa: DTZ007
        except ValueError:
            continue
    raise ValueError(f"since must be a date (YYYY-MM-DD), got {since!r}")


class StaleChecker:
    def __init__(
        self,
        index: Index,
        boe: BOE,
        window_days: int = DEFAULT_WINDOW_DAYS,
        since: str | None = None,
        min_confidence: float = 0.0,
    ) -> None:
        """since: only report changes effective on/after this date (YYYY-MM-DD),
        so a periodic job sees what is new since its last run."""
        self.index = index
        self.boe = boe
        self.window_days = window_days
        self.since = parse_since(since)
        self.min_confidence = min_confidence
        self.today = dt.datetime.now(dt.UTC).strftime("%Y%m%d")
        self._idf = self._load_idf()

    def _load_idf(self) -> dict[str, float]:
        """IDF over facts + question, each consulta counted once (the index
        stores those document frequencies in the `df` table)."""
        con = self.index.con
        self._n = con.execute(
            "SELECT count(*) FROM consulta WHERE marker = 0"
        ).fetchone()[0]
        return {
            t: math.log(self._n / d)
            for t, d in con.execute("SELECT term, df FROM df")
            if d
        }

    def idf(self, term: str) -> float:
        # A term no consulta uses is as rare as it gets.
        return self._idf.get(term, math.log(self._n or 1))

    def _sections(self, path: str) -> dict[str, str]:
        body = frontmatter.load(self.index.corpus / path).content
        return {k: section(body, t) for k, t in SECTIONS.items()}

    # -- signals ---------------------------------------------------------

    def _amendments(self, block: Block, start: str):
        """(previous, new) version pairs that took effect in [start, today]."""
        for prev, cur in zip(block.versions, block.versions[1:]):
            if start <= cur.effective <= self.today and cur.text != prev.text:
                yield prev, cur

    def _relevance(
        self, prev_text: str, new_text: str, consulta_terms: set[str]
    ) -> tuple[float, list[str], list[str]]:
        """(score, rare changed terms shared with the consulta, rare added terms)."""
        old, new = _terms(prev_text), _terms(new_text)
        changed = set((new - old) + (old - new))
        shared = sorted(
            (t for t in changed & consulta_terms if self.idf(t) >= RARE_IDF),
            key=self.idf,
            reverse=True,
        )
        added = sorted(
            (t for t in set(new - old) - consulta_terms if self.idf(t) >= RARE_IDF),
            key=self.idf,
            reverse=True,
        )
        return sum(self.idf(t) for t in shared), shared, added

    def _quoted_change(self, prev_text: str, cur_text: str, quoted: set[int]) -> dict:
        """How the change touched the passage the answer quotes.

        Only edits overlapping (or inserted inside/next to) the quoted word
        positions count. Returns removed/added words, the amounts replaced in
        those edits, and the terms of the edited passages (for topicality).
        """
        pw, cw = WORD_RE.findall(fold(prev_text)), WORD_RE.findall(fold(cur_text))
        covered: set[int] = set()
        for i in range(len(pw) - SHINGLE + 1):
            if hash(tuple(pw[i : i + SHINGLE])) in quoted:
                covered.update(range(i, i + SHINGLE))
        out = {
            "touched": False,
            "removed": [],
            "added": [],
            "old_amounts": set(),
            "new_amounts": set(),
            "passage": Counter(),
        }
        matcher = SequenceMatcher(None, pw, cw, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            span = range(i1, i2) if i2 > i1 else range(i1 - 1, i1 + 1)
            if not any(i in covered for i in span):
                continue
            out["touched"] = True
            out["removed"] += pw[i1:i2]
            out["added"] += cw[j1:j2]
            # Amounts read with a little context, since units usually sit just
            # outside the edit ("[25 → 40] por ciento").
            out["old_amounts"] |= amounts(pw, i1 - 1, i2 + 1)
            out["new_amounts"] |= amounts(cw, j1 - 1, j2 + 1)
            ctx = pw[max(0, i1 - PASSAGE_CONTEXT) : i2 + PASSAGE_CONTEXT]
            out["passage"] += _terms(" ".join(ctx))
        return out

    def _norm_signals(
        self,
        rank: str,
        number: str,
        fecha: str,
        consulta_terms: set[str],
        answer: str,
        answer_shingles: set[int],
    ):
        entry = self.boe.resolve(rank, number)
        if not entry:
            return None, []
        boe_id = entry["identificador"]
        law = f"{rank} {number}"
        signals: list[Signal] = []
        blocks = self.boe.blocks(boe_id)
        boilerplate = self.boe.boilerplate(shingles)

        meta = self.boe.metadata(boe_id)
        repealed = (
            meta.get("fecha_derogacion")
            if meta.get("estatus_derogacion") == "S"
            else None
        )
        if repealed and fecha < repealed <= self.today:
            # Informational: a successor law usually carries the criterion over.
            signals.append(
                Signal(
                    "norm_repealed",
                    law,
                    boe_id,
                    _iso(repealed),
                    "after_consulta",
                    f"{law} repealed with effect from {_iso(repealed)}",
                    0.3,
                    severity="repealed",
                )
            )

        consulta_day = dt.date(int(fecha[:4]), int(fecha[4:6]), int(fecha[6:8]))
        start = (consulta_day - dt.timedelta(days=self.window_days)).strftime("%Y%m%d")
        for block in blocks:
            for prev, cur in self._amendments(block, start):
                quoted = answer_shingles & {hash(sh) for sh in shingles(prev.text)}
                if len(quoted - boilerplate) < MIN_DISTINCTIVE_QUOTE:
                    continue  # the answer doesn't quote this block: not relied upon
                # Boilerplate only decides reliance; once the block is applied,
                # the whole quote counts (quotes often end in a stock phrase,
                # right where an amendment appends a requirement: V1445-22).
                change = self._quoted_change(prev.text, cur.text, quoted)
                touched, removed, added_words = (
                    change["touched"],
                    change["removed"],
                    change["added"],
                )
                score, shared, added = self._relevance(
                    prev.text, cur.text, consulta_terms
                )
                on_topic = sorted(
                    (
                        t
                        for t in set(change["passage"]) & consulta_terms
                        if self.idf(t) >= PASSAGE_MIN_IDF
                    ),
                    key=self.idf,
                    reverse=True,
                )
                amount_swap = (
                    change["old_amounts"]
                    and change["new_amounts"]
                    and change["old_amounts"] != change["new_amounts"]
                )
                if score >= CONCLUSION_SCORE:
                    severity = "conclusion"
                    confidence = min(1.0, 0.5 + score / (4 * RARE_IDF))
                elif amount_swap and on_topic:
                    severity = "reference_or_amount"
                    confidence = 0.6
                    shared = on_topic[:8]
                elif touched:
                    severity = "text_changed"
                    confidence = 0.3
                else:
                    continue  # the quoted passage is intact and the change is off topic
                amending = cur.amended_by
                amending_name = self.boe.citation(amending) if amending else None
                window = (
                    "before_consulta" if cur.effective <= fecha else "after_consulta"
                )
                if window == "before_consulta":
                    confidence *= 0.8  # the DGT may already have taken it into account
                evidence = (
                    f"{block.title} of {law}, quoted in the answer, changed with effect from"
                    f" {_iso(cur.effective)} by {amending_name or amending}"
                )
                if touched:
                    diff = (
                        " ".join(f"-{w}" for w in removed[:6])
                        + " "
                        + " ".join(f"+{w}" for w in added_words[:6])
                    )
                    evidence += f"; quoted passage edited ({diff.strip()})"
                if shared:
                    evidence += f"; the change touches terms of the facts/question: {', '.join(shared[:8])}"
                if amending_name and amending_name in answer:
                    # Cited, but possibly for another provision (V0218-23 cites
                    # Ley 31/2022 for VAT, not for the IAE tariffs it amended).
                    confidence *= 0.7
                    evidence += (
                        f"; the answer cites {amending_name}, check for which provision"
                    )
                signals.append(
                    Signal(
                        "norm_amended",
                        law,
                        boe_id,
                        _iso(cur.effective),
                        window,
                        evidence,
                        round(confidence, 2),
                        block=block.title,
                        amended_by=f"{amending_name} ({amending})"
                        if amending_name
                        else amending or None,
                        severity=severity,
                        shared_terms=shared[:8],
                        added_terms=added[:8],
                    )
                )
        return boe_id, signals

    def _applying_amendment(self, row, signal: Signal) -> list[dict]:
        """Later consultas (same office) that use the amendment's new wording:
        a rare term shared with this consulta plus a rare term the amendment added."""
        if not signal.shared_terms or not signal.added_terms:
            return []
        expr = (
            "("
            + " OR ".join(f'"{t}"' for t in signal.shared_terms[:4])
            + ") AND ("
            + " OR ".join(f'"{t}"' for t in signal.added_terms[:6])
            + ")"
        )
        rows = self.index.con.execute(
            "SELECT c.* FROM consulta_fts JOIN consulta c ON c.id = consulta_fts.rowid "
            "WHERE consulta_fts MATCH ? AND c.marker = 0 AND c.office = ? AND c.date > ? "
            "ORDER BY c.date LIMIT ?",
            (expr, row["office"], row["date"], MAX_LATER),
        ).fetchall()
        return [
            {
                "number": r["number"],
                "date": r["date"],
                "similarity": None,
                "reason": f"uses the wording added to {signal.block} of {signal.law} ({signal.effective})",
                "different_conclusion": None,
            }
            for r in rows
        ]

    def _weighted_jaccard(self, a: set[str], b: set[str]) -> float:
        union = a | b
        return (
            sum(self.idf(t) for t in a & b) / sum(self.idf(t) for t in union)
            if union
            else 0.0
        )

    def _later_consultas(
        self, row, terms: Counter, question_terms: set[str]
    ) -> list[dict]:
        top = sorted(terms, key=self.idf, reverse=True)[:15]
        if not top:
            return []
        expr = "{question facts} : (" + " OR ".join(f'"{t}"' for t in top) + ")"
        rows = self.index.con.execute(
            "SELECT c.* FROM consulta_fts JOIN consulta c ON c.id = consulta_fts.rowid "
            "WHERE consulta_fts MATCH ? AND c.marker = 0 AND c.office = ? AND c.date > ? "
            "ORDER BY bm25(consulta_fts, 4.0, 2.0, 1.0, 2.0) LIMIT 40",
            (expr, row["office"], row["date"]),
        ).fetchall()
        source = {t for t in terms if self.idf(t) >= MIN_TERM_IDF}
        source_q = {t for t in question_terms if self.idf(t) >= MIN_TERM_IDF}
        out = []
        for r in rows:
            secs = self._sections(r["path"])
            other = {
                t
                for t in _terms(secs["facts"] + " " + secs["question"])
                if self.idf(t) >= MIN_TERM_IDF
            }
            other_q = {
                t for t in _terms(secs["question"]) if self.idf(t) >= MIN_TERM_IDF
            }
            sim = self._weighted_jaccard(source, other)
            if (
                sim >= SIMILARITY_THRESHOLD
                and self._weighted_jaccard(source_q, other_q) >= QUESTION_SIMILARITY
            ):
                out.append(
                    {
                        "number": r["number"],
                        "date": r["date"],
                        "similarity": round(sim, 2),
                        "reason": "similar facts and question",
                        "different_conclusion": None,
                    }
                )
        out.sort(key=lambda x: -x["similarity"])
        return out[:MAX_LATER]

    # -- public API ------------------------------------------------------

    def check(self, number: str) -> Result:
        checked_at = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        number = normalize_number(number) or number
        row = self.index._row(number)
        if row is None:
            return Result(
                number, "", checked_at, "insufficient_data", error="unknown consulta"
            )
        if row["marker"]:
            return Result(
                row["number"],
                row["date"],
                checked_at,
                "insufficient_data",
                error="marker entry without content",
            )
        secs = self._sections(row["path"])
        fecha = row["date"].replace("-", "")
        terms = _terms(secs["facts"] + " " + secs["question"])
        consulta_terms = set(terms)

        answer_shingles = {hash(sh) for sh in shingles(secs["answer"])}
        signals: list[Signal] = []
        norms: list[str] = []
        for rank, num in norm_references(
            row["legislation"], secs["answer"], row["date"]
        ):
            boe_id, found = self._norm_signals(
                rank, num, fecha, consulta_terms, secs["answer"], answer_shingles
            )
            if boe_id:
                norms.append(f"{rank} {num} ({boe_id})")
                signals += found
        signals = [
            s
            for s in signals
            if s.confidence >= self.min_confidence
            and (not self.since or s.effective >= self.since)
        ]
        signals.sort(key=lambda s: (SEVERITY_ORDER.get(s.severity, 9), -s.confidence))

        if any(s.severity in STALE_SEVERITIES for s in signals):
            status = "possibly_stale"
        elif norms:
            status = "no_signals"
        else:
            status = "insufficient_data"
        later: dict[str, dict] = {}
        for s in signals:
            for c in self._applying_amendment(row, s):
                later.setdefault(c["number"], c)
        for c in self._later_consultas(row, terms, set(_terms(secs["question"]))):
            later.setdefault(c["number"], c)
        return Result(
            row["number"],
            row["date"],
            checked_at,
            status,
            signals,
            sorted(later.values(), key=lambda c: c["date"]),
            norms,
        )


def to_json(result: Result) -> dict:
    return asdict(result)
