"""Publication status, reviews and the status file."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from dgt_consultas.cli import cli
from dgt_consultas.status import build, label, publication_status, write

FIXTURES = Path(__file__).parent / "fixtures"


def test_publication_status_from_file_names():
    assert publication_status("V0063-24") == ("published", None)
    assert publication_status("V0030-03 Número de consulta anulado") == (
        "annulled",
        None,
    )
    assert publication_status(
        "V0007-03 Consulta sin contenido al ser reemplazada por la consulta número V0152-03"
    ) == ("replaced", "V0152-03")
    assert publication_status(
        "V0053-00 Consulta contestada que no es objeto de publicación"
    ) == (
        "unpublished",
        None,
    )


def _corpus(tmp_path: Path) -> Path:
    (tmp_path / "consultas" / "2024").mkdir(parents=True)
    (tmp_path / "consultas" / "2024" / "V0063-24.md").write_text(
        (FIXTURES / "V0063-24.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (
        tmp_path
        / "consultas"
        / "2024"
        / "V0001-24 Consulta sin contenido al ser reemplazada por la consulta número V0063-24.md"
    ).write_text(
        "---\nnumero: V0001-24 Consulta sin contenido al ser reemplazada por la consulta número V0063-24\n---\n",
        encoding="utf-8",
    )
    return tmp_path


def test_review_overrides_and_file_keeps_only_non_defaults(tmp_path):
    root = _corpus(tmp_path)
    (root / "reviews").mkdir()
    (root / "reviews" / "V0063-24.yaml").write_text(
        "number: V0063-24\nstatus: current\nreviewed: 2026-10-19\nnote: Still applies.\n",
        encoding="utf-8",
    )
    rows = {r.number: r for r in build(root)}
    assert rows["V0063-24"].currency.status == "current"
    replaced = next(r for r in rows.values() if r.publication == "replaced")
    assert (replaced.replaced_by, replaced.currency.status) == (
        "V0063-24",
        "superseded",
    )

    lines = write(root, list(rows.values())).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    by_number = {json.loads(line)["number"]: json.loads(line) for line in lines}
    assert label(by_number["V0063-24"]) == "current (review 2026-10-19)"
    assert label(None) == "unreviewed"


def test_invalid_or_orphan_reviews_fail(tmp_path):
    root = _corpus(tmp_path)
    (root / "reviews").mkdir()
    (root / "reviews" / "V9999-99.yaml").write_text(
        "number: V9999-99\nstatus: current\nreviewed: 2026-10-19\nnote: x\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown consultas"):
        build(root)
    (root / "reviews" / "V9999-99.yaml").unlink()
    (root / "reviews" / "V0063-24.yaml").write_text(
        "number: V0063-24\nstatus: maybe\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="status"):
        build(root)


def test_status_check_detects_stale_file(tmp_path):
    root = _corpus(tmp_path)
    runner = CliRunner()
    assert (
        runner.invoke(cli, ["status", "--check", "--corpus", str(root)]).exit_code == 1
    )
    assert runner.invoke(cli, ["status", "--corpus", str(root)]).exit_code == 0
    assert (
        runner.invoke(cli, ["status", "--check", "--corpus", str(root)]).exit_code == 0
    )
