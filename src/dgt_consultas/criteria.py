"""Explicit DGT statements that change or clarify an earlier consulta.

The DGT announces these with fixed formulas:

  change         "Esta conclusión supone un cambio de criterio respecto al
                 mantenido por esta Dirección General, entre otras, en la
                 contestación vinculante de …, número V0939-11."
  clarification  "Se solicita aclaración de la contestación vinculante …
                 número V2920-18" / "en aclaración de la consulta V1311-08".

Only sentences that use one of these formulas, speak for the DGT itself and
name a consulta number are taken. Each link keeps the DGT's own sentence as
evidence, since a change is often partial ("esta conclusión relativa a …").
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from .index import SECTIONS, fold, section

# Consulta numbers as cited in answers: V0939-11, 1311-08, and the odd CV1905-12.
CITED_RE = re.compile(r"\bC?(V?\d{4}-\d{2})\b", re.IGNORECASE)
CITED_FOLDED_RE = re.compile(r"\bc?(v?\d{4}-\d{2})\b")
# Sentence ends, but not after the abbreviations that precede a number
# ("núm. V0939-11", "art. 20", "pág. 3").
SENTENCE_SPLIT_RE = re.compile(
    r"(?<!\bnúm\.)(?<!\bnum\.)(?<!\bNúm\.)(?<!\bart\.)(?<!\bArt\.)(?<!\bpág\.)"
    r"(?<=[.;])\s+(?=[A-ZÁÉÍÓÚÑ0-9“\"(])"
)

# Only numbers the formula itself presents as the earlier criterion count:
# after "respecto" in "supone un cambio de criterio respecto …", or after
# "recogido/mantenido/… en" in "modifica el criterio recogido en …". A number
# elsewhere in the sentence is often the consulta that adopted the new
# criterion ("…asumida en su contestación V2761-20").
CHANGE_RE = re.compile(
    r"supone (?:un|una) (?:cambio|modificacion) (?:de|del) criterio[^.;]*?\brespecto\b"
    r"|modifica(?:r|ndo)? (?:el|su|este) criterio(?: administrativo)?"
    r"(?: que [a-z ]+?)? (?:recogido|mantenido|manifestado|establecido|seguido|sostenido)"
    r"|modifica(?:r|ndo)? (?:el|su|este) criterio que venia (?:manteniendo|sosteniendo)"
)
# "Esta conclusión no supone un cambio de criterio…" states the opposite.
NEGATED_RE = re.compile(r"\b(?:no|ni|sin) (?:supone|suponer|implica|modifica)\b")
# After the earlier-criterion list, a coordinated clause often names the
# consulta that set the new criterion ("…, y así ha sido puesto de manifiesto …
# en la contestación V2554-23"): stop reading numbers there.
NEW_CRITERION_RE = re.compile(
    r",? y asi\b|puesto de manifiesto|asumid[oa]|adoptad[oa]|como ya\b"
)
# The change must be about the DGT's own doctrine, not a court's or the TEAC's.
# "contestación/consulta vinculante" is DGT-only vocabulary.
OWN_DOCTRINE_RE = re.compile(
    r"este centro directivo|esta direccion general|esta subdireccion"
    r"|direccion general de tributos|(?:contestacion|consulta)(?:es|s)? vinculantes?"
)
CLARIFY_RE = re.compile(
    r"aclaracion de (?:la|las) (?:contestacion|contestaciones|consulta|consultas)"
)


@dataclass(frozen=True)
class Link:
    kind: str  # "changes" | "clarifies"
    source: str  # the later consulta that makes the statement
    target: str  # the earlier consulta it refers to
    date: str  # date of the source
    sentence: str


def _sentences(text: str) -> Iterator[str]:
    yield from SENTENCE_SPLIT_RE.split(" ".join(text.split()))


def links_in(number: str, date: str, body: str) -> Iterator[Link]:
    secs = {k: section(body, t) for k, t in SECTIONS.items()}
    for name, text in secs.items():
        for sentence in _sentences(text):
            folded = fold(sentence)
            cited = [
                c for c in CITED_RE.findall(sentence) if c.upper() != number.upper()
            ]
            if not cited:
                continue
            change = CHANGE_RE.search(folded) if name == "answer" else None
            if change and NEGATED_RE.search(
                folded[max(0, change.start() - 12) : change.end()]
            ):
                change = None
            if change and OWN_DOCTRINE_RE.search(folded):
                # Read the numbers from the folded text itself, so match offsets
                # always line up (folding lowercases the "V").
                rest = folded[change.end() :]
                stop = NEW_CRITERION_RE.search(rest)
                if stop:
                    rest = rest[: stop.start()]
                earlier = [
                    c.upper()
                    for c in CITED_FOLDED_RE.findall(rest)
                    if c.upper() != number.upper()
                ]
                for target in dict.fromkeys(earlier):
                    yield Link("changes", number, target, date, sentence)
            elif CLARIFY_RE.search(folded):
                # A clarification request names the consulta it clarifies first.
                yield Link("clarifies", number, cited[0], date, sentence)
