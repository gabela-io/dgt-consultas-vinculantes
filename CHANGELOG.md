# Changelog

All notable changes to dgt-consultas-vinculantes are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the `dgt-consultas` package uses [Semantic Versioning](https://semver.org/).
Corpus updates are listed with the date of the last PETETE extraction.

## [0.1.0] - Unreleased

Corpus extracted from PETETE up to **2026-10-19**: 70,591 entries (69,253
rulings and 1,338 DGT placeholders), 1997–2026.

### Added

- Scraper for PETETE with an adaptive rate limit, checkpoints, raw HTML cache
  and `--update`, which re-paginates the current year so new rulings are seen.
- Markdown export with YAML frontmatter, pinned byte for byte by a golden test
  against a real PETETE page.
- SQLite FTS5 index with Spanish (Snowball) stemming: `search`, `read`,
  `by-law` and `citations` commands with compact results and snippets.
- MCP server (`dgt-consultas mcp`) exposing the index to agents.
- `stale`: compares the passages a ruling quotes with the BOE consolidated
  texts and reports amendments by severity, repeals, and later rulings from
  the same office. It is evaluated with `scripts/eval_stale.py`.
- Nix flake (blueprint) with dev shell, treefmt formatter and package; CI
  running `nix flake check`.
- Weekly scheduled workflow that fetches new rulings, runs the tests and
  commits them, keeping the README's "Last update" date in sync.
- `validate`: corpus checks (fields, numbers, dates, year directories,
  sections, empty rulings) against a baseline of known upstream issues, run in
  CI and before every weekly commit.
- `refetch`: downloads specific rulings again, finding their current PETETE
  document id by number.
- Atom feeds of the latest rulings, overall and per tax, in `docs/feeds/`.
- Status of every ruling: publication status from the DGT's notices, and
  currency from human reviews (`reviews/`) or DGT replacements, in
  `status/consultas.jsonl`; shown by `read`, `search` and the MCP server.
- Explicit DGT changes of criterion ("supone un cambio de criterio respecto…")
  mark the earlier rulings as superseded (37 today), and clarification
  rulings are listed in `clarified_by` (125 rulings, clarified by 130 later ones).
- `CITATION.cff`.
- Fetch safeguards: HTTP timeouts and TLS verification; a refreshed search
  index that is incomplete or loses known consultas is rejected; a download
  for another consulta, or with less text than the file it would replace, is
  not saved; writes are atomic; `fetch` exits 3 when documents failed.

### Fixed

- Dates written by PETETE as DD-MM-YYYY (V1817-05) are parsed.
- Lookups by number ignore case (PETETE publishes v1649-19 and v1735-25).
- Rulings whose number year is after their issue date (DGT typo, V1536-36) are
  filed by date; replaced-ruling placeholders go in their own year.
- `fetch` no longer replaces a ruling that has content with an empty page.
- Three rulings that had been saved empty (V2284-18, V2362-20, V0840-25) were
  downloaded again with their full text.
