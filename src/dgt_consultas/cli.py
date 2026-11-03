"""CLI for the DGT consultas vinculantes scraper."""

import datetime
import json
import logging
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click

from .checkpoint import atomic_write_text, load_checkpoint, save_checkpoint
from .exporter import save_markdown, save_raw_html
from .notify import notify_ntfy
from .parser import parse_document, parse_search_results
from .scraper import DGTSession

# The repository root is the corpus: consultas/, search_index/, metadata.json.
DEFAULT_DATA_DIR = Path(".")

logger = logging.getLogger("dgt_consultas")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def _load_search_index(data_dir: Path, year: int) -> list[dict] | None:
    """Load cached search index for a year, or None if not cached."""
    path = data_dir / "search_index" / f"{year}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            return None
    return None


def _save_search_index(data_dir: Path, year: int, entries: list[dict]) -> None:
    """Save the full search index for a year (atomically)."""
    atomic_write_text(
        data_dir / "search_index" / f"{year}.json",
        json.dumps(entries, ensure_ascii=False),
    )


def _existing_numeros(data_dir: Path) -> set[str]:
    """Numbers already in the corpus. Only Markdown counts: a raw HTML file
    without its Markdown (crash in between, or a deleted bad file) must be
    fetched again."""
    consultas_dir = data_dir / "consultas"
    if not consultas_dir.exists():
        return set()
    return {p.stem for p in consultas_dir.rglob("*.md")}


def _format_eta(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    hours = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    return f"{hours}h{mins:02d}m"


# A refreshed index must be close to what PETETE announces, and must not lose
# entries we already had: otherwise an error page or a broken pagination run
# would silently hide consultas (and, in CI, get committed).
MIN_INDEX_COMPLETENESS = 0.98
MAX_INDEX_LOSS = 0.02


def _build_search_index(
    session: DGTSession,
    year: int,
    data_dir: Path,
    refresh: bool = False,
) -> list[dict]:
    """Paginate through all search results for a year and return a doc index.

    Uses a cached index if available; otherwise paginates the server and
    caches the result so subsequent runs skip the pagination entirely.
    ``refresh`` ignores the cache: needed for a year PETETE is still
    publishing, whose cached index would hide every newer consulta.

    Raises RuntimeError instead of saving an index that looks incomplete.
    """
    previous = _load_search_index(data_dir, year)
    if previous is not None and not refresh:
        logger.info("[%d] Using cached search index (%d entries)", year, len(previous))
        return previous

    date_start = f"01/01/{year}"
    date_end = f"31/12/{year}"

    html = session.search(page=1, date_start=date_start, date_end=date_end)
    page_data = parse_search_results(html)
    total_pages = page_data.total_pages
    announced = page_data.total_results

    logger.info(
        "[%d] Building search index: %d results across %d pages",
        year,
        announced,
        total_pages,
    )

    entries: list[dict] = [
        {"doc_id": r.doc_id, "numero": r.number} for r in page_data.results
    ]
    for page_num in range(2, total_pages + 1):
        html = session.search(page=page_num, date_start=date_start, date_end=date_end)
        page_data = parse_search_results(html)
        if not page_data.results:
            raise RuntimeError(
                f"[{year}] page {page_num}/{total_pages} came back empty"
            )
        entries.extend(
            {"doc_id": r.doc_id, "numero": r.number} for r in page_data.results
        )
        if page_num % 10 == 0 or page_num == total_pages:
            click.echo(f"[{year}] Indexing page {page_num}/{total_pages}")

    if len(entries) < announced * MIN_INDEX_COMPLETENESS:
        raise RuntimeError(
            f"[{year}] search index incomplete: {len(entries)} of {announced} announced"
        )
    if previous:
        known = {e["numero"] for e in previous if e.get("numero")}
        lost = known - {e["numero"] for e in entries if e.get("numero")}
        if len(lost) > len(known) * MAX_INDEX_LOSS:
            raise RuntimeError(
                f"[{year}] refreshed index lost {len(lost)} of {len(known)} known consultas;"
                " keeping the cached index"
            )

    _save_search_index(data_dir, year, entries)
    logger.info("[%d] Search index cached (%d entries)", year, len(entries))
    return entries


NUMBER_RE = re.compile(r"^V?\d{1,4}-\d{2}( .+)?$", re.IGNORECASE)
MAX_SHRINK = 0.8  # a re-download may not lose more than 20% of a file's text


def _base_number(number: str) -> str:
    """'V0007-03 Consulta sin contenido…' -> 'V0007-03' (case-insensitive key)."""
    return number.split(" ", 1)[0].upper()


def _sections_of(path: Path) -> dict[str, str]:
    import frontmatter

    from .index import SECTIONS, section

    body = frontmatter.load(path).content
    return {k: section(body, heading) for k, heading in SECTIONS.items()}


def _has_content(path: Path) -> bool:
    return any(_sections_of(path).values())


def _safe_save(
    doc_html: str, data_dir: Path, expected: str | None = None, label: str = ""
) -> tuple[str | None, str | None]:
    """Parse a PETETE document and save it, unless that could damage the corpus.

    Refuses (returning an error, writing nothing) when the document is another
    consulta than `expected`, its number or year is malformed, or it would
    replace an existing file with less: a section that had text and is now
    empty, or a text that shrinks by more than 20%. Returns (number, error).
    """
    consulta = parse_document(doc_html)
    if not consulta.number:
        consulta.number = expected or ""
    if not consulta.number:
        return None, f"{label}: document has no number"
    if expected and _base_number(consulta.number) != _base_number(expected):
        return None, f"{label}: expected {expected}, PETETE returned {consulta.number}"
    if not NUMBER_RE.match(consulta.number) or "/" in consulta.number:
        return None, f"{label}: malformed number {consulta.number!r}"
    if not re.fullmatch(r"\d{4}", consulta.year or ""):
        return None, f"{label}: cannot tell the year of {consulta.number}"

    path = data_dir / "consultas" / consulta.year / f"{consulta.number}.md"
    if path.exists():
        old = _sections_of(path)
        new = {
            "facts": consulta.facts,
            "question": consulta.question,
            "answer": consulta.answer,
        }
        lost = [k for k in old if old[k] and not new[k]]
        if lost:
            return (
                None,
                f"{label}: {consulta.number} came back without {', '.join(lost)}; kept {path}",
            )
        old_len, new_len = sum(map(len, old.values())), sum(map(len, new.values()))
        if old_len and new_len < old_len * MAX_SHRINK:
            return (
                None,
                f"{label}: {consulta.number} shrank from {old_len} to {new_len} chars; kept {path}",
            )

    save_markdown(consulta, data_dir)
    save_raw_html(doc_html, consulta.number, consulta.year, data_dir)
    return consulta.number, None


def _fetch_one_doc(
    session: DGTSession,
    doc_id: str,
    number_hint: str,
    data_dir: Path,
    doc_html: str | None = None,
) -> tuple[str | None, str | None]:
    """Fetch (unless `doc_html` is given) and safely save one document.

    Returns (number, error_msg). `number_hint` is the number the search index
    or the caller expects; a document for another consulta is rejected.
    """
    if doc_html is None:
        doc_html = session.fetch_document(doc_id)
    return _safe_save(doc_html, data_dir, number_hint or None, f"document {doc_id}")


def _fetch_year(
    session: DGTSession,
    year: int,
    data_dir: Path,
    force: bool = False,
    existing: set[str] | None = None,
    checkpoint: dict | None = None,
    concurrency: int = 1,
    refresh_index: bool = False,
) -> tuple[int, int]:
    """Fetch all consultas for a given year. Returns (fetched, errors)."""
    if existing is None:
        existing = _existing_numeros(data_dir) if not force else set()
    if checkpoint is None:
        checkpoint = load_checkpoint(data_dir)

    cp_key = f"year_{year}"
    if refresh_index:
        # A checkpoint indexes into the old search index; it's meaningless now.
        checkpoint.pop(cp_key, None)

    entries = _build_search_index(session, year, data_dir, refresh=refresh_index)
    total_results = len(entries)

    if total_results == 0:
        logger.info("[%d] No results found", year)
        return 0, 0

    fetched = 0
    skipped = 0
    errors = 0
    start_time = time.monotonic()

    # Checkpoint: resume from last doc index, but only against the same index
    # it was taken from (positions shift when the index is refreshed).
    saved = checkpoint.get(cp_key, {})
    start_idx = saved.get("doc_idx", 0) if saved.get("entries") == total_results else 0
    if start_idx > 0:
        logger.info("[%d] Resuming from document %d/%d", year, start_idx, total_results)

    # Filter out already-existing entries upfront
    work: list[tuple[int, dict]] = []
    for idx in range(start_idx, total_results):
        entry = entries[idx]
        if entry["numero"] and entry["numero"] in existing:
            logger.debug("Skipping %s (already exists)", entry["numero"])
            skipped += 1
        else:
            work.append((idx, entry))

    if not work:
        logger.info("[%d] All %d consultas already downloaded", year, total_results)
        checkpoint.pop(cp_key, None)
        save_checkpoint(data_dir, checkpoint)
        return 0, 0

    logger.info(
        "[%d] %d to fetch, %d skipped (concurrency=%d)",
        year,
        len(work),
        skipped,
        concurrency,
    )

    # Thread-safe counters
    lock = threading.Lock()

    def _progress() -> None:
        done = fetched + errors
        if done % 20 == 0 or done == len(work):
            elapsed = time.monotonic() - start_time
            rate = fetched / elapsed if elapsed > 0 else 0
            remaining = len(work) - done
            eta_str = _format_eta(remaining / rate) if rate > 0 else "--"
            click.echo(
                f"[{year}] Done {done}/{len(work)} (of {total_results} total) | "
                f"Fetched: {fetched} | "
                f"Skipped: {skipped} | "
                f"Errors: {errors} | "
                f"ETA: {eta_str}"
            )

    first_error_idx: int | None = None
    if concurrency <= 1:
        # Sequential path — simpler, preserves order for checkpointing
        for work_pos, (idx, entry) in enumerate(work):
            try:
                number, err = _fetch_one_doc(
                    session,
                    entry["doc_id"],
                    entry["numero"],
                    data_dir,
                )
                if err or number is None:
                    logger.warning(
                        err or f"Document {entry['doc_id']} returned no number"
                    )
                    errors += 1
                    first_error_idx = (
                        idx if first_error_idx is None else first_error_idx
                    )
                else:
                    existing.add(number)
                    fetched += 1
                    logger.debug("Saved %s", number)
            except Exception:
                logger.exception("Error fetching document %s", entry["doc_id"])
                errors += 1
                first_error_idx = idx if first_error_idx is None else first_error_idx

            _progress()

            if (work_pos + 1) % 20 == 0:
                # Never resume past a failed document: it must be retried.
                resume = idx + 1 if first_error_idx is None else first_error_idx
                checkpoint[cp_key] = {"doc_idx": resume, "entries": total_results}
                save_checkpoint(data_dir, checkpoint)
    else:
        # Concurrent path — pipeline requests to overlap network latency
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            future_to_entry = {}
            for idx, entry in work:
                fut = pool.submit(
                    _fetch_one_doc,
                    session,
                    entry["doc_id"],
                    entry["numero"],
                    data_dir,
                )
                future_to_entry[fut] = (idx, entry)

            for fut in as_completed(future_to_entry):
                idx, entry = future_to_entry[fut]
                try:
                    number, err = fut.result()
                    with lock:
                        if err or number is None:
                            logger.warning(
                                err or f"Document {entry['doc_id']} returned no number"
                            )
                            errors += 1
                        else:
                            existing.add(number)
                            fetched += 1
                            logger.debug("Saved %s", number)
                        _progress()
                except Exception:
                    with lock:
                        logger.exception("Error fetching document %s", entry["doc_id"])
                        errors += 1
                        _progress()

    # The year is done: failed documents are retried on the next run, since
    # they are still missing from consultas/.
    checkpoint.pop(cp_key, None)
    save_checkpoint(data_dir, checkpoint)

    return fetched, errors


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def cli(verbose: bool) -> None:
    """DGT Consultas Vinculantes scraper."""
    _setup_logging(verbose)


@cli.command()
@click.option("--rate-limit", default=0.5, help="Requests per second (default: 0.5)")
def test(rate_limit: float) -> None:
    """Test connectivity to the PETETE server."""
    session = DGTSession(rate_limit=rate_limit)
    click.echo("Initializing session…")
    try:
        session.init()
        click.echo("Session OK. Testing search…")
        html = session.search(page=1)
        page = parse_search_results(html)
        click.echo(
            f"Search OK. Total consultas vinculantes: {page.total_results} "
            f"({page.total_pages} pages)"
        )
        if page.results:
            r = page.results[0]
            click.echo(f"First result: {r.number} (doc_id={r.doc_id})")
            click.echo("Fetching first document…")
            doc_html = session.fetch_document(r.doc_id)
            consulta = parse_document(doc_html)
            click.echo(f"Document OK: {consulta.number} ({consulta.date})")
            click.echo(f"  Office: {consulta.office}")
            click.echo(f"  Legislation: {consulta.legislation}")
            click.echo(f"  Facts: {consulta.facts[:120]}…")
        click.echo("All connectivity tests passed.")
    except Exception as exc:  # noqa: BLE001 — any failure means the probe failed
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cli.command()
@click.option("--year", type=int, help="Download a specific year")
@click.option(
    "--all", "fetch_all", is_flag=True, help="Download all years (1997-present)"
)
@click.option(
    "--update", is_flag=True, help="Download only the current year (incremental)"
)
@click.option("--data-dir", type=click.Path(), default=str(DEFAULT_DATA_DIR))
@click.option(
    "--rate-limit",
    default=1.0,
    help="Starting requests/sec (AIMD adjusts, default: 1.0)",
)
@click.option(
    "--min-rate", default=0.2, help="Minimum requests/sec floor (default: 0.2)"
)
@click.option(
    "--max-rate", default=4.0, help="Maximum requests/sec ceiling (default: 4.0)"
)
@click.option(
    "--concurrency", default=1, help="Concurrent document fetches (default: 1)"
)
@click.option("--force", is_flag=True, help="Re-download even if file exists")
@click.option(
    "--refresh-index",
    is_flag=True,
    help="Re-paginate the PETETE search instead of using search_index/ "
    "(implied by --update)",
)
@click.option(
    "--ntfy-topic",
    default=lambda: os.environ.get("NTFY_TOPIC"),
    help="ntfy topic for a completion notification (or env NTFY_TOPIC)",
)
@click.option(
    "--ntfy-server",
    default=lambda: os.environ.get("NTFY_SERVER"),
    help="ntfy server URL (default: https://ntfy.sh, or env NTFY_SERVER)",
)
def fetch(
    year: int | None,
    fetch_all: bool,
    update: bool,
    data_dir: str,
    rate_limit: float,
    min_rate: float,
    max_rate: float,
    concurrency: int,
    force: bool,
    refresh_index: bool,
    ntfy_topic: str | None,
    ntfy_server: str | None,
) -> None:
    """Fetch consultas vinculantes from PETETE."""
    data_path = Path(data_dir)
    session = DGTSession(rate_limit=rate_limit, min_rate=min_rate, max_rate=max_rate)

    if not year and not fetch_all and not update:
        click.echo("Specify --year YYYY, --all, or --update", err=True)
        sys.exit(1)

    current_year = datetime.datetime.now(datetime.UTC).year
    if year:
        years = [year]
    elif update:
        years = [current_year]
    else:
        years = list(range(1997, current_year + 1))

    existing = _existing_numeros(data_path) if not force else set()
    checkpoint = load_checkpoint(data_path)

    total_fetched = 0
    total_errors = 0
    failed_year: int | None = None
    exc_info: BaseException | None = None

    try:
        for y in years:
            fetched, errors = _fetch_year(
                session,
                y,
                data_path,
                force=force,
                refresh_index=refresh_index or update,
                existing=existing,
                checkpoint=checkpoint,
                concurrency=concurrency,
            )
            total_fetched += fetched
            total_errors += errors
    except BaseException as exc:
        failed_year = y
        exc_info = exc
        raise
    finally:
        if exc_info is None:
            click.echo(f"\nDone. Fetched: {total_fetched} | Errors: {total_errors}")
            _save_metadata(data_path, total_fetched, total_errors)
            year_desc = (
                f"year {years[0]}"
                if len(years) == 1
                else f"years {years[0]}-{years[-1]}"
            )
            priority = "high" if total_errors else "default"
            notify_ntfy(
                ntfy_topic,
                title=f"DGT scraper done ({year_desc})",
                message=f"Fetched: {total_fetched} | Errors: {total_errors}",
                priority=priority,
                server=ntfy_server,
            )
        else:
            notify_ntfy(
                ntfy_topic,
                title="DGT scraper failed",
                message=(
                    f"Aborted on year {failed_year}. "
                    f"Fetched so far: {total_fetched} | Errors: {total_errors}\n"
                    f"{type(exc_info).__name__}: {exc_info}"
                ),
                priority="urgent",
                server=ntfy_server,
            )
    if total_errors:
        # Distinct exit status: the run finished and saved what it could, but
        # some documents failed. The weekly workflow commits and then fails.
        sys.exit(3)


def _save_metadata(data_dir: Path, fetched: int, errors: int) -> None:
    meta_path = data_dir / "metadata.json"
    data_dir.mkdir(parents=True, exist_ok=True)
    # Load existing metadata to accumulate lifetime stats
    meta: dict = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            pass
    meta["last_run"] = datetime.datetime.now(datetime.UTC).isoformat()
    meta["last_fetched"] = fetched
    meta["last_errors"] = errors
    meta["lifetime_fetched"] = meta.get("lifetime_fetched", 0) + fetched
    meta["lifetime_errors"] = meta.get("lifetime_errors", 0) + errors
    atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))


@cli.command()
@click.option("--data-dir", type=click.Path(), default=str(DEFAULT_DATA_DIR))
def stats(data_dir: str) -> None:
    """Show stats about the downloaded corpus."""
    data_path = Path(data_dir)
    consultas_dir = data_path / "consultas"

    if not consultas_dir.exists():
        click.echo("No data found. Run 'fetch' first.")
        return

    total = 0
    by_year: dict[str, int] = {}
    for md in consultas_dir.rglob("*.md"):
        total += 1
        year = md.parent.name
        by_year[year] = by_year.get(year, 0) + 1

    click.echo(f"Total consultas: {total}")
    click.echo(f"Years covered: {len(by_year)}")
    for y in sorted(by_year):
        click.echo(f"  {y}: {by_year[y]}")

    # Size
    total_size = sum(f.stat().st_size for f in consultas_dir.rglob("*.md"))
    if total_size > 1_000_000_000:
        size_str = f"{total_size / 1_000_000_000:.1f} GB"
    elif total_size > 1_000_000:
        size_str = f"{total_size / 1_000_000:.1f} MB"
    else:
        size_str = f"{total_size / 1_000:.1f} KB"
    click.echo(f"Total size: {size_str}")

    # Metadata
    meta_path = data_path / "metadata.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        click.echo(f"Last run: {meta.get('last_run', 'unknown')}")


@cli.command()
@click.option("--data-dir", type=click.Path(), default=str(DEFAULT_DATA_DIR))
def reparse(data_dir: str) -> None:
    """Re-generate all markdown files from cached raw HTML."""
    data_path = Path(data_dir)
    raw_dir = data_path / "raw"

    if not raw_dir.exists():
        click.echo("No raw HTML cache found. Run 'fetch' first.")
        return

    html_files = sorted(raw_dir.rglob("*.html"))
    click.echo(f"Found {len(html_files)} cached HTML files")

    reparsed = 0
    errors = 0
    for html_path in html_files:
        try:
            html_content = html_path.read_text(encoding="utf-8")
            _saved, err = _safe_save(
                html_content, data_path, html_path.stem, str(html_path)
            )
            if err:
                logger.warning(err)
                errors += 1
                continue
            reparsed += 1
        except Exception:
            logger.exception("Error re-parsing %s", html_path)
            errors += 1

    click.echo(f"Done. Re-parsed: {reparsed} | Errors: {errors}")


CORPUS_OPTION = click.option(
    "--corpus",
    type=click.Path(file_okay=False, path_type=Path),
    default=lambda: os.environ.get("DGT_CORPUS", str(DEFAULT_DATA_DIR)),
    help="Corpus root containing consultas/ (or env DGT_CORPUS)",
)
DB_OPTION = click.option(
    "--db",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Index path (default: <corpus>/index/dgt.sqlite)",
)
YEAR_OPTIONS = [
    click.option("--from", "year_from", type=int, help="First year"),
    click.option("--to", "year_to", type=int, help="Last year"),
]
JSON_OPTION = click.option("--json", "as_json", is_flag=True, help="Print raw JSON")


def _with(options):
    def decorate(f):
        for option in reversed(options):
            f = option(f)
        return f

    return decorate


def _open_index(corpus: Path, db: Path | None):
    from .search import Index

    return Index(corpus.resolve(), db)


def _echo_hits(result: dict, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if "notice" in result:
        click.echo(f"# {result['notice']}")
    click.echo(f"# {result['total']} results")
    for h in result["results"]:
        click.echo(f"{h['number']}  {h['date']}  {h['office']}")
        click.echo(f"  status: {h['status']}")
        click.echo(f"  legislation: {h['legislation']}")
        if h.get("snippet"):
            click.echo(f"  {h['snippet']}")


@cli.command()
@CORPUS_OPTION
@DB_OPTION
def index(corpus: Path, db: Path | None) -> None:
    """Build the SQLite search index over consultas/."""
    from .index import build

    corpus = corpus.resolve()
    db = db or corpus / "index" / "dgt.sqlite"
    start = time.monotonic()
    n = build(corpus, db)
    size = db.stat().st_size / 1_000_000
    click.echo(
        f"Indexed {n} files into {db} ({size:.0f} MB) in {time.monotonic() - start:.0f}s"
    )


@cli.command()
@click.argument("query")
@_with(YEAR_OPTIONS)
@click.option("--office", help="Substring of the issuing office (e.g. Consumo, Renta)")
@click.option(
    "--field",
    type=click.Choice(["all", "question", "facts", "answer", "legislation"]),
    default="all",
    show_default=True,
)
@click.option(
    "--match", type=click.Choice(["all", "any"]), default="all", show_default=True
)
@click.option("-n", "--limit", default=10, show_default=True)
@click.option("--offset", default=0)
@JSON_OPTION
@CORPUS_OPTION
@DB_OPTION
def search(
    query, year_from, year_to, office, field, match, limit, offset, as_json, corpus, db
):
    """Ranked search (Spanish stemming); prints number, date, legislation, snippet."""
    idx = _open_index(corpus, db)
    _echo_hits(
        idx.search(query, year_from, year_to, office, field, limit, match, offset),
        as_json,
    )


@cli.command()
@click.argument("number")
@click.option(
    "-s",
    "--section",
    "sections",
    multiple=True,
    type=click.Choice(["facts", "question", "answer"]),
    help="Sections to print (repeatable; default all)",
)
@click.option("--max-chars", default=8000, show_default=True)
@JSON_OPTION
@CORPUS_OPTION
@DB_OPTION
def read(number, sections, max_chars, as_json, corpus, db):
    """Print a consulta's metadata and selected sections."""
    out = _open_index(corpus, db).read(number, list(sections) or None, max_chars)
    if as_json or "error" in out:
        click.echo(json.dumps(out, ensure_ascii=False, indent=2))
        return
    from .status import label

    click.echo(f"{out['number']}  {out['date']}  {out['office']}")
    click.echo(f"status: {label(out.get('status'))}")
    note = ((out.get("status") or {}).get("currency") or {}).get("note")
    if note:
        click.echo(f"review: {note}")
    click.echo(f"legislation: {out['legislation']}\n{out['url']}")
    for name in ("facts", "question", "answer"):
        if name in out:
            click.echo(f"\n## {name}\n{out[name]}")


@cli.command("by-law")
@click.argument("law")
@click.argument("article", required=False)
@_with(YEAR_OPTIONS)
@click.option("-n", "--limit", default=50, show_default=True)
@JSON_OPTION
@CORPUS_OPTION
@DB_OPTION
def by_law(law, article, year_from, year_to, limit, as_json, corpus, db):
    """Consultas whose legislation field mentions LAW [ARTICLE], newest first."""
    idx = _open_index(corpus, db)
    _echo_hits(idx.by_legislation(law, article, year_from, year_to, limit), as_json)


@cli.command()
@click.argument("number")
@CORPUS_OPTION
@DB_OPTION
def citations(number, corpus, db):
    """Consultas cited by NUMBER and consultas citing it."""
    out = _open_index(corpus, db).citations(number)
    click.echo(json.dumps(out, ensure_ascii=False, indent=2))


@cli.command()
@click.argument("numbers", nargs=-1, required=True)
@click.option(
    "--window-days",
    default=365,
    show_default=True,
    help="Also report changes effective this many days before each consulta",
)
@click.option(
    "--since", help="Only report changes effective on/after this date (YYYY-MM-DD)"
)
@click.option("--min-confidence", default=0.0, show_default=True)
@click.option("--offline", is_flag=True, help="Use only the cached BOE data")
@CORPUS_OPTION
@DB_OPTION
def stale(numbers, window_days, since, min_confidence, offline, corpus, db):
    """Check whether consultas may no longer reflect the law in force.

    Prints a JSON array (one object per NUMBER). Exit status 10 when at least
    one consulta is possibly_stale, 0 otherwise. Review-only signals
    (text_changed, repealed) are listed but don't change the status, so a
    consulta can be "no_signals" and still carry them.
    """
    from .boe import BOE
    from .stale import StaleChecker, to_json

    idx = _open_index(corpus, db)
    boe = BOE(idx.corpus / "index" / "boe", offline=offline)
    checker = StaleChecker(idx, boe, window_days, since, min_confidence)
    results = [to_json(checker.check(n)) for n in numbers]
    click.echo(json.dumps(results, ensure_ascii=False, indent=2))
    if any(r["status"] == "possibly_stale" for r in results):
        sys.exit(10)


@cli.command()
@click.option(
    "--out",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("docs/feeds"),
    show_default=True,
)
@click.option(
    "--base-url",
    default="https://gabela-io.github.io/dgt-consultas-vinculantes",
    show_default=True,
    help="Public URL where docs/ is served (GitHub Pages)",
)
@CORPUS_OPTION
def feeds(out, base_url, corpus):
    """Write Atom feeds of the latest consultas (all, and one per office)."""
    from .feeds import write_feeds

    for feed_slug, _title, n in write_feeds(corpus.resolve(), out, base_url):
        click.echo(f"{out / (feed_slug + '.xml')}: {n} entries")


@cli.command()
@click.argument("numbers", nargs=-1, required=True)
@click.option("--data-dir", type=click.Path(), default=str(DEFAULT_DATA_DIR))
@click.option("--rate-limit", default=1.0, help="Requests/sec (default: 1.0)")
def refetch(numbers, data_dir, rate_limit):
    """Download specific consultas again, by number (e.g. to repair empty ones).

    Finds each consulta's current document id with a PETETE search by number,
    and only saves the document if it is the consulta asked for.
    """
    data_path = Path(data_dir)
    session = DGTSession(rate_limit=rate_limit)
    failed = 0
    for number in numbers:
        page = parse_search_results(session.search(number=number))
        doc_id = next(
            (r.doc_id for r in page.results if r.number.upper() == number.upper()), None
        )
        if not doc_id:
            click.echo(f"{number}: not found on PETETE", err=True)
            failed += 1
            continue
        # _safe_save rejects a document for another consulta, so what is checked
        # is exactly what gets saved (one download per number).
        _saved, err = _fetch_one_doc(session, doc_id, number, data_path)
        click.echo(f"{number}: {err or f'saved (document {doc_id})'}")
        failed += bool(err)
    if failed:
        sys.exit(1)


@cli.command()
@click.option(
    "--check", is_flag=True, help="Fail if status/consultas.jsonl is out of date"
)
@CORPUS_OPTION
def status(check, corpus):
    """Write status/consultas.jsonl: publication status and reviewed currency."""
    from .status import build, write

    corpus = corpus.resolve()
    path = corpus / "status" / "consultas.jsonl"
    before = path.read_text(encoding="utf-8") if path.exists() else None
    try:
        rows = build(corpus)
    except ValueError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    if check:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            fresh = write(Path(tmp), rows).read_text(encoding="utf-8")
        if fresh != before:
            click.echo(
                "status/consultas.jsonl is out of date: run `dgt-consultas status`",
                err=True,
            )
            sys.exit(1)
        click.echo(f"status up to date ({len(rows)} consultas)")
        return
    write(corpus, rows)
    reviewed = sum(r.currency.source == "review" for r in rows)
    click.echo(f"Wrote {path} ({len(rows)} consultas, {reviewed} reviewed)")


@cli.command()
@click.option(
    "--baseline",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Known issues to tolerate (default: <corpus>/validation-baseline.json)",
)
@click.option(
    "--write-baseline", is_flag=True, help="Accept every current issue as known"
)
@CORPUS_OPTION
def validate(baseline, write_baseline, corpus):
    """Check the corpus for malformed files; exit 1 on issues not in the baseline."""
    from .validate import load_baseline
    from .validate import validate as run
    from .validate import write_baseline as save

    corpus = corpus.resolve()
    baseline = baseline or corpus / "validation-baseline.json"
    issues = run(corpus)
    if write_baseline:
        save(baseline, issues)
        click.echo(f"Wrote {len(issues)} known issues to {baseline}")
        return
    known = load_baseline(baseline)
    new = [i for i in issues if i not in known]
    fixed = sorted(known - set(issues))
    for issue in new:
        click.echo(f"NEW   {issue}")
    for issue in fixed:
        click.echo(f"FIXED {issue} (remove it from the baseline)")
    click.echo(
        f"{len(issues)} issues: {len(issues) - len(new)} known, {len(new)} new, {len(fixed)} fixed"
    )
    if new:
        sys.exit(1)


@cli.command()
@CORPUS_OPTION
@DB_OPTION
def mcp(corpus: Path, db: Path | None) -> None:
    """Run the MCP server (stdio) over the index."""
    from .mcp_server import build_server

    build_server(corpus.resolve(), db).run()


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
