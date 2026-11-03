# dgt-consultas-vinculantes

default:
    @just --list

# Run the test suite
test *args:
    uv run pytest {{ args }}

# Lint
lint:
    uv run --with ruff ruff check src tests

# Format everything (nix, python, yaml, toml, markdown, shell)
fmt:
    nix fmt

# Every check CI runs
check:
    nix flake check --log-format bar-with-logs

# Build the package
build:
    nix build .#dgt-consultas --log-format bar-with-logs

# Check that PETETE is reachable and parseable
ping:
    uv run dgt-consultas test

# Download the consultas of the current year into consultas/
update *args:
    uv run dgt-consultas fetch --update {{ args }}

# Download a single year into consultas/
fetch year *args:
    uv run dgt-consultas fetch --year {{ year }} {{ args }}

# Corpus stats by year
stats:
    uv run dgt-consultas stats

# Build the SQLite search index in index/
index:
    uv run dgt-consultas index

# Search the index (e.g. just search "modelo 347" --from 2022)
search query *args:
    uv run dgt-consultas search {{ quote(query) }} {{ args }}

# Read one consulta (e.g. just read V0063-24 -s answer)
read number *args:
    uv run dgt-consultas read {{ number }} {{ args }}

# Consultas whose normativa cites a law and optional article
by-law law *args:
    uv run dgt-consultas by-law {{ law }} {{ args }}

# Staleness report as JSON (exit 10 if any consulta is possibly stale)
stale *numbers:
    uv run dgt-consultas stale {{ numbers }}
