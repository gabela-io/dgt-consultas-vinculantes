"""Stale-consulta signals against a fake BOE cache (no network)."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from dgt_consultas import stale as stale_mod
from dgt_consultas.boe import BOE, _legal_paragraphs
from dgt_consultas.cli import cli
from dgt_consultas.index import build
from dgt_consultas.search import Index
from dgt_consultas.stale import StaleChecker, amounts, norm_references

IRPF = "SG de Impuestos sobre la Renta de las Personas Físicas"
GROUP_861 = "Grupo 861. Pintores, escultores, ceramistas, artesanos, grabadores y artistas similares."
ART_188_OLD = (
    "El importe de la sanción que deba ingresarse por la comisión de cualquier infracción "
    "se reducirá en el 25 por ciento si concurren las siguientes circunstancias."
)
ART_188_NEW = ART_188_OLD.replace("25 por ciento", "40 por ciento")
NOTE = '<blockquote><p class="parrafo">Téngase en cuenta que esta modificación entra en vigor. Ref. BOE-A-2021-1</p></blockquote>'


def _consulta(number, date, facts, question, answer, legislation="TRLRHL", office=IRPF):
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


def _norm(boe_id, rank, number):
    return {
        "identificador": boe_id,
        "rango": {"texto": rank},
        "numero_oficial": number,
        "ambito": {"codigo": "1"},
    }


def _boe_cache(path: Path) -> None:
    path.mkdir(parents=True)
    catalogue = [
        _norm("BOE-A-1990-23930", "Real Decreto Legislativo", "1175/1990"),
        _norm("BOE-A-2022-22128", "Ley", "31/2022"),
        _norm("BOE-A-2003-23186", "Ley", "58/2003"),
        _norm("BOE-A-2004-4214", "Real Decreto Legislativo", "2/2004"),
    ]
    (path / "catalogue-0.json").write_text(json.dumps({"data": catalogue}))
    (path / "BOE-A-1990-23930.texto.xml").write_text(
        f"""<response><data><texto>
<bloque id="agrupacion86-2" tipo="precepto" titulo="Agrupación 86">
  <version id_norma="BOE-A-1990-23930" fecha_vigencia="19901022"><p>{GROUP_861}</p></version>
  <version id_norma="BOE-A-2022-22128" fecha_vigencia="20230101"><p>{GROUP_861}</p>
    <p>Grupo 864. Escritores y guionistas.</p>{NOTE}</version>
</bloque></texto></data></response>""",
        encoding="utf-8",
    )
    (path / "BOE-A-2003-23186.texto.xml").write_text(
        f"""<response><data><texto>
<bloque id="a188" tipo="precepto" titulo="Artículo 188">
  <version id_norma="BOE-A-2003-23186" fecha_vigencia="20040701"><p>{ART_188_OLD}</p></version>
  <version id_norma="BOE-A-2021-11473" fecha_vigencia="20210711"><p>{ART_188_NEW}</p>{NOTE}</version>
</bloque></texto></data></response>""",
        encoding="utf-8",
    )
    for boe_id in (
        "BOE-A-1990-23930",
        "BOE-A-2022-22128",
        "BOE-A-2003-23186",
        "BOE-A-2004-4214",
    ):
        (path / f"{boe_id}.metadatos.json").write_text(
            json.dumps({"data": [{"estatus_derogacion": "N"}]})
        )
    for boe_id in ("BOE-A-2022-22128", "BOE-A-2004-4214"):
        (path / f"{boe_id}.texto.xml").write_text(
            "<response><data><texto/></data></response>"
        )


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("corpus")
    for year in ("2008", "2023", "2024"):
        (root / "consultas" / year).mkdir(parents=True)
    write = lambda year, number, text: (
        root / "consultas" / year / f"{number}.md"
    ).write_text(text, encoding="utf-8")
    write(
        "2023",
        "V0218-23",
        _consulta(
            "V0218-23",
            "2023-02-13",
            "Soy un escritor que publica sus libros en plataformas de autopublicación.",
            "Tributación en el IAE.",
            "El Real Decreto Legislativo 1175/1990 aprueba las Tarifas. "
            f"El grupo 861 clasifica la actividad de {GROUP_861} "
            "La Ley 31/2022 modifica el artículo 70 de la Ley 37/1992.",
        ),
    )
    write(
        "2024",
        "V1435-24",
        _consulta(
            "V1435-24",
            "2024-06-14",
            "El consultante publica artículos en revistas jurídicas.",
            "Tributación en el IAE.",
            "Debe darse de alta en el grupo 864 Escritores y guionistas.",
        ),
    )
    write(
        "2008",
        "V2210-08",
        _consulta(
            "V2210-08",
            "2008-11-24",
            "Presentó una declaración complementaria y pagó la sanción reducida.",
            "Reducción de la sanción por pronto pago.",
            f"Según la Ley 58/2003, artículo 188: {ART_188_OLD}",
            legislation="Ley 58/2003",
            office="SG de Tributos",
        ),
    )
    write(
        "2008",
        "V2211-08",
        _consulta(
            "V2211-08",
            "2008-11-25",
            "Una sociedad agrícola pregunta por sus cooperativas.",
            "Régimen de las cooperativas agrarias.",
            f"Se transcribe la Ley 58/2003, artículo 188: {ART_188_OLD}",
            legislation="Ley 58/2003",
            office="SG de Tributos",
        ),
    )
    write(
        "2024",
        "V0001-24 Número de consulta anulado",
        _consulta("V0001-24 Número de consulta anulado", "2024-01-02", "", "", ""),
    )
    build(root, root / "index" / "dgt.sqlite")
    _boe_cache(root / "index" / "boe")
    return root


@pytest.fixture
def tiny_corpus_thresholds(monkeypatch):
    # A five-document corpus has tiny IDFs; scale the rarity bars down.
    monkeypatch.setattr(stale_mod, "RARE_IDF", 0.5)
    monkeypatch.setattr(stale_mod, "CONCLUSION_SCORE", 0.5)
    monkeypatch.setattr(stale_mod, "PASSAGE_MIN_IDF", 0.5)


@pytest.fixture
def checker(corpus, tiny_corpus_thresholds) -> StaleChecker:
    return StaleChecker(Index(corpus), BOE(corpus / "index" / "boe", offline=True))


def test_norm_references_from_field_and_answer():
    refs = norm_references(
        "TRLRHL. LIVA",
        "según el Real Decreto Legislativo 1175/1990 y la Ley 31/2022",
        "2023-02-13",
    )
    assert ("Real Decreto Legislativo", "1175/1990") in refs
    assert ("Ley", "31/2022") in refs
    assert ("Real Decreto Legislativo", "2/2004") in refs  # TRLRHL
    assert ("Ley", "37/1992") in refs  # LIVA


def test_amounts_need_a_unit():
    words = [
        "se",
        "reducira",
        "en",
        "el",
        "25",
        "por",
        "ciento",
        "y",
        "en",
        "el",
        "apartado",
        "3",
        "o",
        "la",
        "casilla",
        "61",
    ]
    assert amounts(words, 0, len(words)) == {"25", "61"}


def test_editorial_notes_are_not_legal_text():
    import xml.etree.ElementTree as ET

    version = ET.fromstring(
        f"<version><p>Texto legal.</p>{NOTE}<p class='nota_pie'>Se modifica…</p></version>"
    )
    assert ["".join(p.itertext()) for p in _legal_paragraphs(version)] == [
        "Texto legal."
    ]


def test_acceptance_case(checker):
    r = checker.check("V0218-23")
    assert r.status == "possibly_stale"
    sig = r.signals[0]
    assert (sig.kind, sig.severity) == ("norm_amended", "conclusion")
    assert sig.law == "Real Decreto Legislativo 1175/1990"
    assert sig.block == "Agrupación 86"
    assert sig.effective == "2023-01-01"
    assert sig.window == "before_consulta"
    assert sig.amended_by == "Ley 31/2022 (BOE-A-2022-22128)"
    assert "escritor" in sig.shared_terms
    assert (
        "cites Ley 31/2022" in sig.evidence
    )  # the answer cites the amending law for VAT
    assert [c["number"] for c in r.later_consultas] == ["V1435-24"]


def test_amount_in_quoted_passage_on_topic(checker):
    r = checker.check("V2210-08")
    assert r.status == "possibly_stale"
    assert (r.signals[0].severity, r.signals[0].block) == (
        "reference_or_amount",
        "Artículo 188",
    )
    assert "-25" in r.signals[0].evidence and "+40" in r.signals[0].evidence


def test_amount_in_merely_transcribed_passage(checker):
    # Same quoted article and the same edit, but the question is about something
    # else: reported for review, without changing the status.
    r = checker.check("V2211-08")
    assert r.status == "no_signals"
    assert [s.severity for s in r.signals] == ["text_changed"]


def test_since_filters_old_changes(corpus, tiny_corpus_thresholds):
    late = StaleChecker(
        Index(corpus), BOE(corpus / "index" / "boe", offline=True), since="2024-01-01"
    )
    assert late.check("V0218-23").status == "no_signals"


def test_unquoted_blocks_are_ignored(checker):
    assert checker.check("V1435-24").status != "possibly_stale"


def test_markers_and_unknown_numbers(checker):
    assert (
        checker.check("V0001-24 Número de consulta anulado").status
        == "insufficient_data"
    )
    assert checker.check("V9999-99").error == "unknown consulta"


def test_cli_json_array_and_exit_code(corpus, tiny_corpus_thresholds):
    res = CliRunner().invoke(
        cli, ["stale", "V0218-23", "V1435-24", "--offline", "--corpus", str(corpus)]
    )
    assert res.exit_code == 10
    out = json.loads(res.output)
    assert [r["number"] for r in out] == ["V0218-23", "V1435-24"]
    assert out[0]["status"] == "possibly_stale"
    assert out[0]["signals"][0]["severity"] == "conclusion"


def test_since_accepts_both_date_forms_and_rejects_others():
    from dgt_consultas.stale import parse_since

    assert parse_since("20240101") == parse_since("2024-01-01") == "2024-01-01"
    with pytest.raises(ValueError):
        parse_since("01/01/2024")
