"""HTML parsing for DGT PETETE search results and document pages."""

import html
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup


@dataclass
class SearchResult:
    doc_id: str
    number: str
    preview_facts: str
    preview_question: str


@dataclass
class SearchPage:
    total_results: int
    total_pages: int
    results: list[SearchResult]


@dataclass
class Consulta:
    number: str
    office: str
    date: str  # DD/MM/YYYY as returned by PETETE
    legislation: str
    facts: str
    question: str
    answer: str

    @property
    def date_iso(self) -> str:
        """Convert DD/MM/YYYY (or DD-MM-YYYY, as PETETE sometimes writes it) to YYYY-MM-DD."""
        parts = re.split(r"[/-]", self.date.strip())
        if len(parts) == 3 and len(parts[0]) <= 2 and len(parts[2]) == 4:
            return f"{parts[2]}-{parts[1]}-{parts[0]}"
        return self.date

    @property
    def year(self) -> str:
        """Extract 4-digit year from the consulta number (e.g. V2653-24 -> 2024).

        A number can't belong to a year after its issue date: when it does,
        it's a DGT typo (V1536-36, issued on 2026-06-15), so the date's year
        wins. The number itself is kept as published.
        """
        date_year = (
            self.date_iso[:4]
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.date_iso)
            else ""
        )
        m = re.search(r"-(\d{2})$", self.number)
        if m:
            yy = int(m.group(1))
            year = str(2000 + yy) if yy < 80 else str(1900 + yy)
            if date_year and year > date_year:
                return date_year
            return year
        # Fallback: use date
        return date_year or self.date_iso[:4]


def _clean_html(text: str) -> str:
    """Strip HTML tags and unescape entities."""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_search_results(html_content: str) -> SearchPage:
    """Parse a search results page and return structured data."""
    # Total results: updateNumResults("2", "NNNNN")
    total_match = re.search(r'updateNumResults\("2",\s*"(\d+)"\)', html_content)
    if not total_match:
        # An error or maintenance page served with 200 OK: not "no results".
        raise ValueError("unexpected PETETE search page: no result count")
    total_results = int(total_match.group(1))

    # Total pages
    pages_match = re.search(r'<span id="total_pages">(\d+)</span>', html_content)
    total_pages = int(pages_match.group(1)) if pages_match else 1

    # Document entries
    results: list[SearchResult] = []
    doc_pattern = re.compile(
        r'<td id="doc_(\d+)"[^>]*onClick="return viewDocument\(\d+,\s*2\);">'
    )
    soup = BeautifulSoup(html_content, "html.parser")

    for td in soup.find_all("td", id=re.compile(r"^doc_\d+$")):
        td_id = td.get("id", "")
        doc_id = td_id.replace("doc_", "")

        # Walk up to the containing row / sibling area to find metadata
        container = td.parent or td
        num_span = container.find("span", class_="NUM-CONSULTA")
        number = ""
        if num_span:
            strong = num_span.find("strong")
            number = (
                strong.get_text(strip=True) if strong else num_span.get_text(strip=True)
            )

        facts_span = container.find("span", class_="DESCRIPCION-HECHOS")
        preview_facts = facts_span.get_text(strip=True) if facts_span else ""

        question_span = container.find("span", class_="CUESTION-PLANTEADA")
        preview_question = question_span.get_text(strip=True) if question_span else ""

        results.append(
            SearchResult(
                doc_id=doc_id,
                number=number,
                preview_facts=preview_facts,
                preview_question=preview_question,
            )
        )

    # Fallback: use regex if BS4 found nothing (some pages have odd HTML)
    if not results:
        for m in doc_pattern.finditer(html_content):
            doc_id = m.group(1)
            results.append(
                SearchResult(
                    doc_id=doc_id, number="", preview_facts="", preview_question=""
                )
            )

    return SearchPage(
        total_results=total_results, total_pages=total_pages, results=results
    )


def _extract_multi_paragraph(soup: BeautifulSoup, class_name: str) -> str:
    """Extract and join all <p> elements with the given class."""
    paragraphs = soup.find_all("p", class_=class_name)
    parts: list[str] = []
    for p in paragraphs:
        text = _clean_html(str(p))
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _extract_single_field(soup: BeautifulSoup, class_name: str) -> str:
    """Extract a single-value field."""
    tag = soup.find("p", class_=class_name)
    if tag:
        return _clean_html(str(tag))
    # Fallback: look in <tr> with the class
    tr = soup.find("tr", class_=class_name)
    if tr:
        return _clean_html(tr.get_text())
    return ""


def parse_document(html_content: str) -> Consulta:
    """Parse a full document page into a Consulta."""
    soup = BeautifulSoup(html_content, "html.parser")
    return Consulta(
        number=_extract_single_field(soup, "NUM-CONSULTA"),
        office=_extract_single_field(soup, "ORGANO"),
        date=_extract_single_field(soup, "FECHA-SALIDA"),
        legislation=_extract_multi_paragraph(soup, "NORMATIVA"),
        facts=_extract_multi_paragraph(soup, "DESCRIPCION-HECHOS"),
        question=_extract_multi_paragraph(soup, "CUESTION-PLANTEADA"),
        answer=_extract_multi_paragraph(soup, "CONTESTACION-COMPL"),
    )
