"""Atom feeds: grouping by office, determinism, valid XML."""

import xml.etree.ElementTree as ET
from pathlib import Path

from dgt_consultas.feeds import ATOM, office_feed, write_feeds

BASE = "https://example.org/dgt"


def _consulta(root: Path, number: str, date: str, office: str) -> None:
    year = date[:4]
    (root / "consultas" / year).mkdir(parents=True, exist_ok=True)
    (root / "consultas" / year / f"{number}.md").write_text(
        f"""---
fecha: '{date}'
normativa: Ley 37/1992
numero: {number}
organo: {office}
url: https://petete.tributos.hacienda.gob.es/consultas/?num_consulta={number}
---

# Consulta Vinculante {number}

## Descripcion de hechos

Hechos.

## Cuestion planteada

Cuestión de {number}.

## Contestacion

Respuesta.
""",
        encoding="utf-8",
    )


def test_office_feeds_absorb_typos_and_renames():
    assert office_feed("SG de Impuestos sobre el Consumo")[0] == "iva"
    assert (
        office_feed("G de Impuestos sobre la Renta de las Personas Físicas")[0]
        == "irpf"
    )
    assert office_feed("SG de Operaciones Financieras")[0] == "operaciones-financieras"
    assert (
        office_feed("SG de Tributación de las Operaciones Financieras")[0]
        == "operaciones-financieras"
    )
    assert office_feed("SG de Tributos")[0] == "tributos"


def test_write_feeds(tmp_path):
    _consulta(tmp_path, "V0001-26", "2026-01-10", "SG de Impuestos sobre el Consumo")
    _consulta(
        tmp_path,
        "V0002-26",
        "2026-02-10",
        "SG de Impuestos sobre la Renta de las Personas Físicas",
    )
    _consulta(tmp_path, "V0003-25", "2025-12-01", "SG de Impuestos sobre el Consumo")
    (
        tmp_path / "consultas" / "2026" / "V0004-26 Número de consulta anulado.md"
    ).write_text("---\nfecha: '2026-03-01'\nnumero: V0004-26\n---\n", encoding="utf-8")
    out = tmp_path / "docs" / "feeds"
    summary = {s: n for s, _t, n in write_feeds(tmp_path, out, BASE)}
    assert summary == {"all": 3, "irpf": 1, "iva": 2}

    root = ET.parse(out / "all.xml").getroot()
    ids = [e.findtext(f"{{{ATOM}}}id") for e in root.findall(f"{{{ATOM}}}entry")]
    assert ids[0].endswith("V0002-26")  # newest first; the marker is left out
    assert len(ids) == 3
    assert (tmp_path / "docs" / "index.html").exists()

    before = {p.name: p.read_bytes() for p in out.iterdir()}
    write_feeds(tmp_path, out, BASE)
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before
