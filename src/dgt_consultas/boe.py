"""Client for the BOE consolidated-legislation open data API, with a disk cache.

Three resources are used:
  - the catalogue of consolidated norms, to resolve "Ley 37/1992" to its
    BOE id (state-level norms only);
  - /id/{id}/texto, where every block carries every version it ever had,
    each with the amending norm and its effective date;
  - /id/{id}/metadatos, for repeal status and dates.

Responses are cached under <cache_dir>; parsed block versions are cached as
JSON so a law's XML (several MB for LIVA/LIRPF) is parsed only once.
"""

from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import requests

from .checkpoint import atomic_write_text

API = "https://www.boe.es/datosabiertos/api/legislacion-consolidada"
TTL_SECONDS = 7 * 24 * 3600
REQUEST_DELAY = 0.5
STATE_SCOPE = "1"  # ambito.codigo for state-level (Estatal) norms
# Bump when _parse_blocks changes, so cached parses are rebuilt.
PARSE_VERSION = 2
EDITORIAL_RE = re.compile(r"Téngase en cuenta|Ref\. BOE-")


@dataclass
class Version:
    effective: str  # YYYYMMDD
    amended_by: str  # BOE id of the norm that produced this version
    text: str


@dataclass
class Block:
    id: str
    title: str
    versions: list[Version]  # sorted by effective date


class BOE:
    def __init__(self, cache_dir: Path, offline: bool = False) -> None:
        self.cache_dir = cache_dir
        self.offline = offline
        self._catalogue: dict[tuple[str, str], dict] | None = None
        self._blocks: dict[str, list[Block]] = {}

    # -- transport -------------------------------------------------------

    def _get(self, url: str, accept: str) -> bytes:
        resp = requests.get(url, headers={"Accept": accept}, timeout=120)
        resp.raise_for_status()
        time.sleep(REQUEST_DELAY)
        return resp.content

    def _cached(self, name: str, url: str, accept: str) -> bytes | None:
        path = self.cache_dir / name
        fresh = path.exists() and time.time() - path.stat().st_mtime < TTL_SECONDS
        if fresh or (self.offline and path.exists()):
            return path.read_bytes()
        if self.offline:
            return None
        body = self._get(url, accept)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return body

    # -- catalogue -------------------------------------------------------

    def catalogue(self) -> dict[tuple[str, str], dict]:
        """(rank, official number) -> norm metadata, for state-level norms."""
        if self._catalogue is not None:
            return self._catalogue
        entries: list[dict] = []
        offset = 0
        while True:
            body = self._cached(
                f"catalogue-{offset}.json",
                f"{API}?limit=10000&offset={offset}",
                "application/json",
            )
            page = json.loads(body)["data"] if body else []
            if not isinstance(page, list) or not page:
                break
            entries += page
            if len(page) < 10000:
                break
            offset += 10000
        self._catalogue = {
            (e["rango"]["texto"], e["numero_oficial"]): e
            for e in entries
            if e.get("ambito", {}).get("codigo") == STATE_SCOPE
            and e.get("numero_oficial")
        }
        return self._catalogue

    def resolve(self, rank: str, number: str) -> dict | None:
        return self.catalogue().get((rank, number))

    def citation(self, boe_id: str) -> str | None:
        """'BOE-A-2022-22128' -> 'Ley 31/2022' (state-level norms only)."""
        for (rank, number), entry in self.catalogue().items():
            if entry["identificador"] == boe_id:
                return f"{rank} {number}"
        return None

    def metadata(self, boe_id: str) -> dict:
        body = self._cached(
            f"{boe_id}.metadatos.json",
            f"{API}/id/{boe_id}/metadatos",
            "application/json",
        )
        if not body:
            return {}
        data = json.loads(body).get("data") or {}
        return data[0] if isinstance(data, list) and data else data

    # -- block versions --------------------------------------------------

    def blocks(self, boe_id: str) -> list[Block]:
        """Every 'precepto' block of a norm with all its versions."""
        if boe_id in self._blocks:
            return self._blocks[boe_id]
        parsed = self.cache_dir / f"{boe_id}.blocks.v{PARSE_VERSION}.json"
        raw_name = f"{boe_id}.texto.xml"
        raw_path = self.cache_dir / raw_name
        # The parsed form is only a cache of the raw text: reuse it while the
        # raw text is within its TTL (or offline), so amendments show up.
        raw_fresh = raw_path.exists() and (
            self.offline or time.time() - raw_path.stat().st_mtime < TTL_SECONDS
        )
        if (
            raw_fresh
            and parsed.exists()
            and parsed.stat().st_mtime >= raw_path.stat().st_mtime
        ):
            data = json.loads(parsed.read_text(encoding="utf-8"))
        else:
            body = self._cached(raw_name, f"{API}/id/{boe_id}/texto", "application/xml")
            data = self._parse_blocks(body) if body else []
            if body:
                atomic_write_text(parsed, json.dumps(data, ensure_ascii=False))
        blocks = [
            Block(b["id"], b["title"], [Version(**v) for v in b["versions"]])
            for b in data
        ]
        self._blocks[boe_id] = blocks
        return blocks

    def boilerplate(self, shingler, min_blocks: int = 3) -> set[int]:
        """Hashes of word runs that appear in at least min_blocks distinct blocks
        across every cached law: law titles, "de acuerdo con lo previsto en"…
        A quote made only of these says nothing about which provision applies."""
        # Keyed by the cached laws (and their versions), so the result doesn't
        # depend on which consultas were checked earlier in the same process.
        paths = sorted(self.cache_dir.glob(f"*.blocks.v{PARSE_VERSION}.json"))
        key = tuple((p.name, p.stat().st_mtime_ns) for p in paths)
        if getattr(self, "_boilerplate_key", None) == key:
            return self._boilerplate
        counts: dict[int, int] = {}
        for path in paths:
            for block in json.loads(path.read_text(encoding="utf-8")):
                seen = set()
                for v in block["versions"]:
                    seen |= {hash(sh) for sh in shingler(v["text"])}
                for h in seen:
                    counts[h] = counts.get(h, 0) + 1
        self._boilerplate = {h for h, n in counts.items() if n >= min_blocks}
        self._boilerplate_key = key
        return self._boilerplate

    @staticmethod
    def _parse_blocks(body: bytes) -> list[dict]:
        root = ET.fromstring(body)
        out = []
        for b in root.iter("bloque"):
            if b.get("tipo") != "precepto":
                continue
            versions = []
            for v in b.findall("version"):
                text = " ".join(
                    " ".join("".join(p.itertext()).split())
                    for p in _legal_paragraphs(v)
                )
                versions.append(
                    {
                        "effective": v.get("fecha_vigencia")
                        or v.get("fecha_publicacion")
                        or "",
                        "amended_by": v.get("id_norma") or "",
                        "text": text,
                    }
                )
            versions.sort(key=lambda x: x["effective"])
            out.append(
                {
                    "id": b.get("id"),
                    "title": b.get("titulo") or "",
                    "versions": versions,
                }
            )
        return out


def _legal_paragraphs(version: ET.Element):
    """Paragraphs of the legal text, without the BOE's editorial notes.

    Footnotes (class nota_pie*) and "Téngase en cuenta…" blockquotes describe
    the amendment history and change with every version; comparing them would
    flag every amended block as rewritten.
    """
    for child in version:
        if child.tag == "blockquote" and EDITORIAL_RE.search("".join(child.itertext())):
            continue
        for p in child.iter("p"):
            if (p.get("class") or "").startswith("nota_pie"):
                continue
            yield p
