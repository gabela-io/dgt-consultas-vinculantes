"""Corpus validation and the empty-document guard."""

from pathlib import Path

from click.testing import CliRunner

from dgt_consultas.cli import _fetch_one_doc, cli
from dgt_consultas.validate import Issue, validate

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = (FIXTURES / "V0063-24.md").read_text(encoding="utf-8")


def _corpus(tmp_path: Path) -> Path:
    (tmp_path / "consultas" / "2024").mkdir(parents=True)
    (tmp_path / "consultas" / "2024" / "V0063-24.md").write_text(GOOD, encoding="utf-8")
    return tmp_path


def test_clean_corpus_has_no_issues(tmp_path):
    assert validate(_corpus(tmp_path)) == []


def test_detects_regressions(tmp_path):
    root = _corpus(tmp_path)
    bad = GOOD.replace("fecha: '2024-02-15'", "fecha: 15-02-2024").replace(
        "## Contestacion", "## Respuesta"
    )
    (root / "consultas" / "2024" / "V0064-24.md").write_text(
        bad.replace("V0063-24", "V0064-24"), encoding="utf-8"
    )
    (root / "consultas" / "2023").mkdir()
    (root / "consultas" / "2023" / "V0063-24.md").write_text(GOOD, encoding="utf-8")
    codes = {(i.code, i.path) for i in validate(root)}
    assert ("bad_date", "consultas/2024/V0064-24.md") in codes
    assert ("missing_section", "consultas/2024/V0064-24.md") in codes
    assert ("wrong_year_dir", "consultas/2023/V0063-24.md") in codes


def test_cli_fails_only_on_new_issues(tmp_path):
    root = _corpus(tmp_path)
    empty = (
        GOOD.split("## Descripcion de hechos")[0]
        + "## Descripcion de hechos\n\n## Cuestion planteada\n\n## Contestacion\n"
    )
    (root / "consultas" / "2024" / "V0065-24.md").write_text(
        empty.replace("V0063-24", "V0065-24"), encoding="utf-8"
    )
    runner = CliRunner()
    assert runner.invoke(cli, ["validate", "--corpus", str(root)]).exit_code == 1
    assert (
        runner.invoke(
            cli, ["validate", "--write-baseline", "--corpus", str(root)]
        ).exit_code
        == 0
    )
    res = runner.invoke(cli, ["validate", "--corpus", str(root)])
    assert res.exit_code == 0 and "1 known, 0 new" in res.output
    assert Issue("empty_consulta", "consultas/2024/V0065-24.md") in validate(root)


class _EmptyPetete:
    def fetch_document(self, doc_id):
        return "<html><p class='NUM-CONSULTA'>V0063-24</p></html>"


def test_empty_document_never_overwrites_content(tmp_path):
    root = _corpus(tmp_path)
    number, err = _fetch_one_doc(_EmptyPetete(), "1", "V0063-24", root)
    assert number is None and "came back without" in err
    assert (root / "consultas" / "2024" / "V0063-24.md").read_text(
        encoding="utf-8"
    ) == GOOD
