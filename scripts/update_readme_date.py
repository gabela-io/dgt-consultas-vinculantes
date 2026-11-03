"""Set the README's "Last update" date from metadata.json.

The Ministerio de Hacienda's reuse terms require stating when the data was
last updated, so the scheduled update keeps this line in sync.

    uv run python scripts/update_readme_date.py
"""

import datetime as dt
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAST_UPDATE_RE = re.compile(r"Last update: \*\*[^*]+\*\*")


def main() -> None:
    meta = json.loads((ROOT / "metadata.json").read_text(encoding="utf-8"))
    day = dt.datetime.fromisoformat(meta["last_run"]).date()
    label = f"Last update: **{day:%B} {day.day}, {day.year}**"
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    new, count = LAST_UPDATE_RE.subn(label, text)
    if count != 1:
        raise SystemExit(f"expected one 'Last update' line in README.md, found {count}")
    if new != text:
        readme.write_text(new, encoding="utf-8")
    print(label)


if __name__ == "__main__":
    main()
