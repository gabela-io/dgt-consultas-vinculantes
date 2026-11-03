"""Index and search over a tiny corpus."""

import shutil
from pathlib import Path

import pytest

from dgt_consultas.index import build
from dgt_consultas.search import Index

FIXTURES = Path(__file__).parent / "fixtures"


def _consulta(number, date, office, legislation, facts, question, answer):
    return f"""---
fecha: '{date}'
normativa: {legislation}
numero: {number}
organo: {office}
url: https://petete.tributos.hacienda.gob.es/consultas/?num_consulta={number}
---

# Consulta Vinculante {number}

## Descripcion de hechos

{facts}

## Cuestion planteada

{question}

## Contestacion

{answer}
"""


@pytest.fixture(scope="module")
def idx(tmp_path_factory) -> Index:
    root = tmp_path_factory.mktemp("corpus")
    (root / "consultas" / "2024").mkdir(parents=True)
    (root / "consultas" / "2025").mkdir(parents=True)
    shutil.copy(FIXTURES / "V0063-24.md", root / "consultas" / "2024" / "V0063-24.md")
    (root / "consultas" / "2025" / "V0100-25.md").write_text(
        _consulta(
            "V0100-25",
            "2025-03-01",
            "SG de Impuestos sobre el Consumo",
            "Ley 37/1992 art. 164",
            "Una sociedad vende criptomonedas a clientes de otros Estados miembros.",
            "Obligación de presentar el modelo 347 y el modelo 349.",
            "Las operaciones declaradas en el modelo 349 no se incluyen en el 347. "
            "Véase la consulta V0063-24.",
        ),
        encoding="utf-8",
    )
    (root / "consultas" / "2025" / "V0101-25 Número de consulta anulado.md").write_text(
        _consulta(
            "V0101-25 Número de consulta anulado",
            "2025-03-02",
            "SG de Impuestos sobre el Consumo",
            "Ley 37/1992 art. 164",
            "",
            "Modelo 347 anulado.",
            "Números sin contenido: V0063-24, V0100-25.",
        ),
        encoding="utf-8",
    )
    build(root, root / "index" / "dgt.sqlite")
    return Index(root)


def numbers(result):
    return [h["number"] for h in result["results"]]


def test_stemming_matches_singular_and_plural(idx):
    assert numbers(idx.search("criptomoneda")) == ["V0100-25"]
    assert numbers(idx.search("CRIPTOMONEDAS")) == ["V0100-25"]


def test_accents_are_ignored(idx):
    assert numbers(idx.search("exencion")) == ["V0063-24"]


def test_markers_are_excluded(idx):
    assert numbers(idx.search("modelo 347")) == ["V0100-25"]


def test_snippet_is_short_and_relevant(idx):
    hit = idx.search("seguro asistencia sanitaria")["results"][0]
    assert hit["number"] == "V0063-24"
    assert "sanitaria" in hit["snippet"].lower()
    assert len(hit["snippet"]) <= 310


def test_filters(idx):
    assert numbers(idx.search("modelo", year_from=2025)) == ["V0100-25"]
    assert numbers(idx.search("modelo", office="Renta")) == []
    assert numbers(idx.search("349", field="question")) == ["V0100-25"]
    assert numbers(idx.search("despido", field="question")) == []


def test_phrase_and_fallback(idx):
    assert numbers(idx.search('"modelo 349"')) == ["V0100-25"]
    res = idx.search("criptomonedas despido")
    assert res["match"] == "any"
    assert "notice" in res
    assert set(numbers(res)) == {"V0063-24", "V0100-25"}


def test_lookup_by_number_variants(idx):
    for q in ("V0063-24", "v63-24", "63/24"):
        assert numbers(idx.search(q)) == ["V0063-24"]


def test_read_sections_and_truncation(idx):
    out = idx.read("V0063-24", ["question"])
    assert set(out) >= {"number", "date", "legislation", "question"}
    assert "answer" not in out
    short = idx.read("V0063-24", ["answer"], max_chars=100)
    assert "truncated" in short["answer"]
    assert idx.read("V9999-99")["error"]


def test_by_legislation(idx):
    assert numbers(idx.by_legislation("37/1992", "164")) == ["V0100-25"]
    assert numbers(idx.by_legislation("37/1992", "16")) == []
    assert numbers(idx.by_legislation("Ley 35/2006")) == ["V0063-24"]


def test_citations(idx):
    # The marker lists V0063-24 too, but markers never count as citing.
    assert [c["number"] for c in idx.citations("V0063-24")["cited_by"]] == ["V0100-25"]
    assert idx.citations("v100-25")["cites"][0]["number"] == "V0063-24"


def test_info_counts_markers_apart(idx):
    info = idx.info()
    assert info["consultas"] == 2
    assert info["markers"] == 1


def test_prefix_queries_are_stemmed(idx):
    assert numbers(idx.search("criptomonedas*")) == ["V0100-25"]
    assert numbers(idx.search("cripto*")) == ["V0100-25"]
    assert idx.search("a*")["total"] == 0  # too short: would match everything


@pytest.mark.parametrize(
    "query",
    [
        "OR",
        "NOT",
        "NOT modelo",
        "modelo NOT",
        "modelo OR",
        "modelo AND AND 349",
        "modelo OR de",
    ],
)
def test_stray_operators_never_crash(idx, query):
    result = idx.search(query)
    assert "error" not in result


def test_limits_are_clamped(idx):
    assert len(idx.search("modelo", limit=-1)["results"]) == 1
    assert idx.search("modelo", offset=-5)["total"] >= 1
    assert idx.read("V0100-25", max_chars=-50)["facts"].startswith("… [truncated")


def test_markers_are_found_by_their_number(idx):
    assert idx.read("V0101-25")["number"].startswith("V0101-25 ")
    assert idx.read("v0100-25")["number"] == "V0100-25"


def test_unknown_number_falls_back_to_text(idx):
    assert idx.search("37/92")["match"] != "number"


def test_article_does_not_match_law_number_or_dates(idx):
    assert idx.by_legislation("37/1992", "37")["total"] == 0
    assert idx.by_legislation("37/1992", "164")["total"] == 1
    with pytest.raises(ValueError):
        idx.by_legislation("%")
