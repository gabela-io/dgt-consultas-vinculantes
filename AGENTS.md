# dgt-consultas-vinculantes

Scraper (`dgt-consultas`, Python) plus the full scraped corpus of DGT consultas vinculantes from PETETE.

## Layout

| Path | What |
|---|---|
| `src/dgt_consultas/` | scraper: `cli` (click), `scraper` (PETETE session + retries), `parser` (HTML → `Consulta`), `exporter` (`Consulta` → Markdown), `ratelimit` (AIMD token bucket), `retry`, `checkpoint`, `notify` (ntfy); search: `index` (builds SQLite + FTS5), `search` (`Index` query API), `mcp_server` (MCP tools over `Index`); staleness: `boe` (BOE open-data client + cache), `stale` (signals); status: `status` (status/consultas.jsonl from data, reviews and DGT statements), `criteria` (explicit change/clarification statements); `validate` (corpus checks + baseline); `feeds` (Atom feeds in docs/) |
| `tests/` | pytest; `fixtures/V0063-24.{html,md}` is a real PETETE page and its corpus file |
| `consultas/<year>/<numero>.md` | the corpus (generated, ~850 MB, ~70k files) |
| `search_index/<year>.json` | cached PETETE search pagination (`doc_id` ↔ `numero`) |
| `metadata.json` | last-run / lifetime fetch counters |
| `raw/`, `checkpoint.json` | gitignored scraper state |
| `index/dgt.sqlite` | gitignored search index (`dgt-consultas index`, ~6 min, ~290 MB) |
| `index/boe/` | gitignored BOE cache (catalogue, `/texto` XML + parsed block versions, `/metadatos`), 7-day TTL |
| `.claude/skills/`, `skills-lock.json` | vendored agent skills (`npx skills`), excluded from formatting |
| `nix/` | blueprint (`prefix = "nix"`): `devshell.nix`, `formatter.nix`, `packages/dgt-consultas/` |

## Commands

| Command | What |
|---|---|
| `just test` | pytest via uv |
| `just fmt` | `nix fmt` (treefmt) |
| `just check` | `nix flake check`: package build + tests + format check |
| `just update` | re-paginate PETETE and fetch the current year into the repo root |
| `just stats` | corpus counts per year |
| `just index` | rebuild the search index (after every corpus update) |
| `just search Q` / `just read N` / `just by-law L [A]` | query the index from the shell |
| `just stale N…` | JSON staleness report; exit 10 if any `possibly_stale` |
| `dgt-consultas mcp` | MCP server (stdio) with `search`, `read`, `by_legislation`, `citations`, `stale`, `info` |

## Invariants

- The repo root is the scraper's data dir (`DEFAULT_DATA_DIR = "."`). Run the CLI from the root.
- The Markdown layout (frontmatter keys, the three unaccented `## ` headings) is a public format consumed downstream (downstream dataset builders and RAG indexers match the headings literally). `tests/test_parser.py::test_markdown_matches_corpus` pins it byte for byte; changing the exporter means regenerating the corpus (`dgt-consultas reparse` needs `raw/`).
- The corpus is never formatted: `consultas/`, `search_index/`, `metadata.json` and `tests/fixtures/` are excluded in `nix/formatter.nix`.
- Nix sources are filtered with `lib.fileset`, so the package and the format check don't copy the corpus and don't rebuild after a scrape. Evaluating the flake itself still copies the whole git tree to the store, so the first `nix` command after a corpus change is slow.
- Python deps resolve twice: uv (`uv.lock`) for the local venv, nixpkgs for the Nix build. Keep `pyproject.toml` bounds compatible with nixpkgs-unstable.
- `search_index/<year>.json` is a cache: `fetch` reuses it unless `--refresh-index` (implied by `--update`) is given. A year PETETE is still publishing must be re-indexed, or new consultas are invisible.
- `.github/workflows/update.yml` fetches new consultas every Monday, runs the tests and pushes as `github-actions[bot]` (unsigned, by design). It updates the README's "Last update" date through `scripts/update_readme_date.py`, which the Hacienda reuse terms require. Counts in the README and banner are generic ("nearly 70,000"), so they don't need updating. `fetch` exits 3 when some documents failed: the workflow still commits what was saved, then fails. A refreshed search index that is incomplete or loses known consultas is rejected, and a re-download never replaces a file with less text. In January it also re-checks the previous year.
- `validate` fails only on issues not in `validation-baseline.json` (empty rulings and similar problems that come from PETETE itself). When a baseline entry gets fixed, remove it; when PETETE adds a genuinely broken ruling, `dgt-consultas validate --write-baseline` accepts it, after checking it's not a scraper bug.
- PETETE document ids in old `search_index/*.json` files can point to other rulings (the DGT renumbers them). `refetch` looks the current id up by number and refuses to save a document whose number differs. `fetch` never overwrites a file that has content with an empty document.
- `feeds` writes Atom feeds to `docs/feeds/` (GitHub Pages serves `docs/`). Offices are grouped through `feeds.OFFICE_FEEDS`, which absorbs typos and renames. Output is deterministic, so a run without new rulings changes nothing.
- Status lives outside the Markdown (`status/consultas.jsonl`, `reviews/*.yaml`) because the official text must stay unaltered. The file only lists consultas that differ from the default (published, unreviewed). Currency is set only from named evidence: a review, a DGT replacement, or an explicit DGT change of criterion (`criteria.py`), in that order of precedence. Automatic `stale` signals are never written as status. CI runs `status --check`.
- `criteria.py` only accepts the DGT's fixed formulas ("supone un cambio de criterio respecto…", "modifica el criterio recogido en…") spoken by the DGT about itself, and only the numbers the formula presents as the earlier criterion. Numbers elsewhere in the sentence, or after "y así…/puesto de manifiesto…", are often the consulta that adopted the new criterion; `tests/test_criteria.py` pins those cases.
- Reviews are public: write neutral legal notes. Never include anyone's personal tax situation or private context.
- Code, comments, docs and CLI/MCP names are in English. The corpus format stays Spanish: frontmatter keys (`numero`, `fecha`, `organo`, `normativa`), section headings and `search_index/*.json` keys. Code maps them to English names (`number`, `date`, `office`, `legislation`, `facts`, `question`, `answer`).
- The index stores no document text: an FTS5 table over Snowball-stemmed text (contentless) plus metadata. Snippets and sections are read from the Markdown, so the corpus stays the single source of truth. Rebuild the index after every scrape.
- `mcp` is pinned below 2.x because nixpkgs ships 1.x and 2.x renamed `FastMCP`.
- Directory year = the number's year (`Consulta.year`), unless that year is after the issue date (a DGT typo, e.g. V1536-36 issued 2026-06-15), in which case the date's year. Replaced-consulta placeholders go in their own number's year, not the replacement's.
- A few consultas (8, from 2002–2006) have no `V` prefix (`0027-06`), and `0027-06` ≠ `V0027-06`: number lookups keep the prefix as typed and only fall back to the other form if it doesn't exist. Placeholders are named with the DGT notice (`V0007-03 Consulta sin contenido…`) but are looked up by the leading number (the index's `base` column).
- `stale` never parses references into structured articles. Law identifiers ("Ley 31/2022", acronyms in `normativa`) are matched by pattern, like V-numbers. A BOE block counts as applied only if the answer quotes at least two distinctive 6-word runs of it; runs found in 3+ blocks of the cached laws are boilerplate. BOE editorial notes (`nota_pie*`, "Téngase en cuenta…" blockquotes) are stripped before comparing versions; bump `boe.PARSE_VERSION` when parsing changes. Severities and thresholds live at the top of `stale.py`. Acceptance cases are in `tests/test_stale.py`: V0218-23 → Agrupación 86 / Ley 31/2022, and the 25 → 40 % amount change.
- PETETE's TLS chain is often broken; `DGTSession` sets `verify = False` on purpose.
