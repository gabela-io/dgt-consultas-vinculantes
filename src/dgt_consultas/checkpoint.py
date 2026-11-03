"""Generic JSON checkpoint helpers for resumable scrapes."""

from __future__ import annotations

import json
import os
from pathlib import Path


def load_checkpoint(data_dir: Path) -> dict:
    """Load a checkpoint dict from ``<data_dir>/checkpoint.json``, or {}."""
    cp_path = data_dir / "checkpoint.json"
    if cp_path.exists():
        try:
            return json.loads(cp_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            return {}
    return {}


def save_checkpoint(data_dir: Path, checkpoint: dict) -> None:
    """Persist a checkpoint dict to ``<data_dir>/checkpoint.json``.

    Uses atomic write (temp file + rename) so a crash mid-write
    won't corrupt the checkpoint file.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    cp_path = data_dir / "checkpoint.json"
    tmp_path = cp_path.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(str(tmp_path), str(cp_path))


def atomic_write_text(path: Path, text: str) -> None:
    """Write a file so that readers see either the old or the new content, never
    a truncated one (temp file in the same directory + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
