"""Search-index caching and safety in the fetch pipeline."""

import json

import pytest

from dgt_consultas.cli import _build_search_index, _safe_save
from dgt_consultas.parser import parse_search_results


def _page(numbers, announced=None, pages=1):
    rows = "".join(
        f'<tr><td id="doc_{i}" onClick="return viewDocument({i}, 2);">'
        f'<span class="NUM-CONSULTA"><strong>{n}</strong></span></td></tr>'
        for i, n in enumerate(numbers, start=1)
    )
    count = len(numbers) if announced is None else announced
    return (
        f'<script>updateNumResults("2", "{count}")</script>'
        f'<span id="total_pages">{pages}</span><table>{rows}</table>'
    )


class FakeSession:
    def __init__(self, pages):
        self.pages = pages
        self.searches = 0

    def search(self, page, date_start=None, date_end=None, number=None):
        self.searches += 1
        return self.pages[page - 1]


def _write_index(tmp_path, entries):
    (tmp_path / "search_index").mkdir()
    (tmp_path / "search_index" / "2026.json").write_text(json.dumps(entries))


def _cached(tmp_path):
    return json.loads((tmp_path / "search_index" / "2026.json").read_text())


def test_cached_index_is_reused(tmp_path):
    _write_index(tmp_path, [{"doc_id": "1", "numero": "V0001-26"}])
    session = FakeSession([_page(["V0002-26"])])
    entries = _build_search_index(session, 2026, tmp_path)
    assert session.searches == 0
    assert [e["numero"] for e in entries] == ["V0001-26"]


def test_refresh_rewrites_cache_with_new_consultas(tmp_path):
    _write_index(tmp_path, [{"doc_id": "1", "numero": "V0001-26"}])
    session = FakeSession([_page(["V0001-26", "V0002-26"])])
    entries = _build_search_index(session, 2026, tmp_path, refresh=True)
    assert [e["numero"] for e in entries] == ["V0001-26", "V0002-26"]
    assert _cached(tmp_path) == entries


def test_refresh_that_loses_known_consultas_is_rejected(tmp_path):
    known = [{"doc_id": str(i), "numero": f"V{i:04d}-26"} for i in range(1, 11)]
    _write_index(tmp_path, known)
    session = FakeSession([_page(["V0001-26"])])
    with pytest.raises(RuntimeError, match="lost"):
        _build_search_index(session, 2026, tmp_path, refresh=True)
    assert _cached(tmp_path) == known  # cache untouched


def test_incomplete_pagination_is_rejected(tmp_path):
    session = FakeSession([_page(["V0001-26"], announced=50, pages=2), _page([])])
    with pytest.raises(RuntimeError, match="came back empty"):
        _build_search_index(session, 2026, tmp_path, refresh=True)
    session = FakeSession([_page(["V0001-26"], announced=50)])
    with pytest.raises(RuntimeError, match="incomplete"):
        _build_search_index(session, 2026, tmp_path, refresh=True)
    assert not (tmp_path / "search_index" / "2026.json").exists()


def test_error_page_is_not_zero_results():
    with pytest.raises(ValueError, match="no result count"):
        parse_search_results("<html><h1>Servicio no disponible</h1></html>")


DOC = """<p class="NUM-CONSULTA">{n}</p><p class="FECHA-SALIDA">15/02/2024</p>
<p class="DESCRIPCION-HECHOS">{facts}</p><p class="CUESTION-PLANTEADA">Cuestión.</p>
<p class="CONTESTACION-COMPL">{answer}</p>"""


def test_safe_save_rejects_other_consulta_and_shrinking(tmp_path):
    full = DOC.format(n="V0063-24", facts="Hechos completos.", answer="Respuesta " * 50)
    assert _safe_save(full, tmp_path, "V0063-24") == ("V0063-24", None)

    other = DOC.format(n="V9999-24", facts="x", answer="y")
    number, err = _safe_save(other, tmp_path, "V0063-24")
    assert number is None and "PETETE returned V9999-24" in err

    truncated = DOC.format(n="V0063-24", facts="Hechos completos.", answer="Respuesta")
    number, err = _safe_save(truncated, tmp_path, "V0063-24")
    assert number is None and "shrank" in err

    no_answer = DOC.format(n="V0063-24", facts="Hechos completos.", answer="")
    number, err = _safe_save(no_answer, tmp_path, "V0063-24")
    assert number is None and "without answer" in err
    assert (
        "Respuesta Respuesta"
        in (tmp_path / "consultas" / "2024" / "V0063-24.md").read_text()
    )
