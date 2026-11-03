"""Parser and exporter tests against a real PETETE document."""

from pathlib import Path

from dgt_consultas.exporter import consulta_to_markdown, save_markdown
from dgt_consultas.parser import Consulta, parse_document, parse_search_results

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_document_fields():
    c = parse_document(_fixture("V0063-24.html"))
    assert c.number == "V0063-24"
    assert c.date == "15/02/2024"
    assert c.date_iso == "2024-02-15"
    assert c.year == "2024"
    assert c.office == "SG de Impuestos sobre la Renta de las Personas Físicas"
    assert c.legislation == "Ley 35/2006, Arts.17.1 y 42.3.c)"
    assert c.facts.startswith("El consultante extinguió su relación laboral")
    assert c.question.startswith("Aplicación a las primas del seguro")
    assert c.answer.endswith("(BOE del día 18).")


def test_markdown_matches_corpus():
    """The exporter must reproduce the committed corpus byte for byte."""
    c = parse_document(_fixture("V0063-24.html"))
    assert consulta_to_markdown(c) == _fixture("V0063-24.md")


def test_save_markdown_layout(tmp_path):
    c = parse_document(_fixture("V0063-24.html"))
    path = save_markdown(c, tmp_path)
    assert path == tmp_path / "consultas" / "2024" / "V0063-24.md"


def test_year_from_number_century():
    base = {
        "office": "",
        "legislation": "",
        "facts": "",
        "question": "",
        "answer": "",
    }
    assert Consulta(number="V0001-97", date="", **base).year == "1997"
    assert Consulta(number="V0001-24", date="", **base).year == "2024"


def test_year_number_typo_falls_back_to_date():
    base = {"office": "", "legislation": "", "facts": "", "question": "", "answer": ""}
    # DGT typo: V1536-36 was issued in 2026.
    assert Consulta(number="V1536-36", date="15/06/2026", **base).year == "2026"
    # Late publication is normal: a 2025 number issued in 2026 keeps 2025.
    assert Consulta(number="V2647-25", date="10/01/2026", **base).year == "2025"


def test_parse_search_results():
    html = """
    <script>updateNumResults("2", "1234")</script>
    <span id="total_pages">62</span>
    <table><tr>
      <td id="doc_98765" onClick="return viewDocument(98765, 2);">
        <span class="NUM-CONSULTA"><strong>V0063-24</strong></span>
        <span class="DESCRIPCION-HECHOS">El consultante...</span>
        <span class="CUESTION-PLANTEADA">Aplicación...</span>
      </td>
    </tr></table>
    """
    page = parse_search_results(html)
    assert page.total_results == 1234
    assert page.total_pages == 62
    assert [(r.doc_id, r.number) for r in page.results] == [("98765", "V0063-24")]


def test_date_iso_accepts_dashes():
    base = {"office": "", "legislation": "", "facts": "", "question": "", "answer": ""}
    assert (
        Consulta(number="V1817-05", date="19-09-2005", **base).date_iso == "2005-09-19"
    )
    assert (
        Consulta(number="V0063-24", date="15/02/2024", **base).date_iso == "2024-02-15"
    )
