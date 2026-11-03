"""Export consultas to Markdown files."""

import logging
from pathlib import Path

import frontmatter

from .checkpoint import atomic_write_text
from .index import base_number
from .parser import Consulta

logger = logging.getLogger(__name__)

BASE_URL = "https://petete.tributos.hacienda.gob.es"


def consulta_to_markdown(c: Consulta) -> str:
    """Render a Consulta as a Markdown string with YAML frontmatter."""
    body = "\n".join(
        [
            f"# Consulta Vinculante {c.number}",
            "",
            "## Descripcion de hechos",
            "",
            c.facts,
            "",
            "## Cuestion planteada",
            "",
            c.question,
            "",
            "## Contestacion",
            "",
            c.answer,
            "",
        ]
    )
    # Frontmatter keys are the published corpus format: keep them in Spanish.
    post = frontmatter.Post(
        body,
        numero=c.number,
        organo=c.office,
        fecha=c.date_iso,
        normativa=c.legislation,
        # Placeholders keep the DGT notice in `numero`; the link uses the number.
        url=f"{BASE_URL}/consultas/?num_consulta={base_number(c.number)}",
    )
    return frontmatter.dumps(post) + "\n"


def save_markdown(c: Consulta, data_dir: Path) -> Path:
    """Save a consulta as a Markdown file, returning the path."""
    year_dir = data_dir / "consultas" / c.year
    year_dir.mkdir(parents=True, exist_ok=True)
    path = year_dir / f"{c.number}.md"
    atomic_write_text(path, consulta_to_markdown(c))
    return path


def save_raw_html(html_content: str, number: str, year: str, data_dir: Path) -> Path:
    """Save the raw HTML of a document for later re-parsing."""
    raw_dir = data_dir / "raw" / year
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{number}.html"
    atomic_write_text(path, html_content)
    return path


def load_raw_html(number: str, year: str, data_dir: Path) -> str | None:
    """Load cached raw HTML for a document, or None if not cached."""
    path = data_dir / "raw" / year / f"{number}.html"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None
