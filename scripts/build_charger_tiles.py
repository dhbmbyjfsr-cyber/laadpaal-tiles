#!/usr/bin/env python3
"""Static charger tiles for the Laadpaalprijzen app (OSM ODbL).

The app asks for one grid cell at a time. A cell is 0.12° lat × 0.18° lng,
the same grid as ChargerStore._cellDeg for the osm feed:

  x = floor(lng / 0.18), y = floor(lat / 0.12)
  GET {base}/v1/index.json         regions this build covers
  GET {base}/v1/cells/{x}/{y}.json list of Charger.toJson maps

A 404 for a cell inside a covered region means: no public chargers there.
Each charger in a cell matches Charger.toJson in the app (see
lib/models/charger.dart) and is built the same way as
lib/data/osm_charger_client.dart builds it from QLever/Overpass.

Inputs (any mix, repeatable):
  --geojsonseq F   output of scripts/pbf_chargers_geojsonseq.py on a Geofabrik
                   extract (after `osmium tags-filter ... nwr/amenity=charging_station`)
  --overpass-json F  Overpass `out tags center` JSON (local tests)
  --region ID:S,W,N,E  a bbox this build fully covers (repeatable)

Usage:
  python3 scripts/build_charger_tiles.py --out tiles \\
      --geojsonseq be.geojsonseq --region be:49.49,2.54,51.51,6.41

Stdlib only: the PBF work happens before this script.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

CELL_LAT = 0.12
CELL_LNG = 0.18
FORMAT_VERSION = 1
# Same as chargers.PRIVATE_ACCESS: "customers" (supermarkets) stays on the map.
PRIVATE_ACCESS = frozenset({"private", "no"})

_KWH_RE = re.compile(r"(\d+[.,]\d+|\d+)\s*(?:€|eur|euro)?\s*/\s*kwh", re.IGNORECASE)
_NUM_RE = re.compile(r"(\d+(?:[.,]\d+)?)")
_FREE_WORDS = frozenset({"gratuit", "gratuite", "free", "gratis", "kostenlos"})

# (app connector name, socket key, power type) — same order as the app.
_SOCKETS = (
    ("type2", "socket:type2", "AC"),
    ("ccs", "socket:ccs", "DC"),
    ("chademo", "socket:chademo", "DC"),
    ("type2_combo", "socket:type2_combo", "DC"),
)


def cell_of(lat: float, lng: float) -> tuple[int, int]:
    """(x, y) like ChargerStore._tileKeys: floor(lng/0.18), floor(lat/0.12)."""
    return math.floor(lng / CELL_LNG), math.floor(lat / CELL_LAT)


def parse_explicit_kwh_price(raw: str) -> Optional[float]:
    """Explicit €/kWh only, like parseExplicitKwhPrice in the app."""
    text = (raw or "").strip()
    if not text:
        return None
    if text.lower() in _FREE_WORDS:
        return 0.0
    match = _KWH_RE.search(text)
    if match is None:
        return None
    try:
        value = float(match.group(1).replace(",", "."))
    except ValueError:
        return None
    if value < 0 or value > 3.0:
        return None
    return value


def official_price(tags: dict[str, str]) -> Optional[float]:
    if (tags.get("fee") or "").strip().lower() == "no":
        return 0.0
    return parse_explicit_kwh_price(tags.get("charge") or "")


def _kilowatts(raw: str) -> Optional[float]:
    if not raw or not raw.strip():
        return None
    match = _NUM_RE.search(raw)
    if match is None:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


def connectors(tags: dict[str, str]) -> list[list[Any]]:
    out: list[list[Any]] = []
    for name, key, power_type in _SOCKETS:
        count_raw = (tags.get(key) or "").strip()
        power_raw = (tags.get(f"{key}:output") or "").strip()
        if not count_raw and not power_raw:
            continue
        try:
            count = int(count_raw)
        except ValueError:
            count = 1
        out.append([0, max(count, 1), _kilowatts(power_raw) or 0, name, power_type, []])
    return out


def _first_of(tags: dict[str, str], keys: Iterable[str]) -> str:
    for key in keys:
        value = (tags.get(key) or "").strip()
        if value:
            return value
    return ""


def is_public(tags: dict[str, str]) -> bool:
    return (tags.get("access") or "yes").strip().lower() not in PRIVATE_ACCESS


def charger_json(
    osm_type: str, osm_id: int | str, lat: float, lng: float, tags: dict[str, str]
) -> dict[str, Any]:
    """One charger in the app's Charger.toJson shape."""
    address = ", ".join(
        p for p in ((tags.get("addr:street") or "").strip(), (tags.get("addr:city") or "").strip()) if p
    )
    return {
        "id": f"osm-{osm_type}-{osm_id}",
        "lat": round(lat, 6),
        "lng": round(lng, 6),
        "a": address,
        "o": _first_of(tags, ("operator", "brand", "network", "name")),
        "c": "",
        "k": connectors(tags),
        "p": official_price(tags),
        "e": False,
    }


# ---- inputs -----------------------------------------------------------------

_TYPE_PREFIX = {"n": "node", "w": "way", "r": "relation"}


def _flatten_coords(coords: Any) -> Iterator[tuple[float, float]]:
    if (
        isinstance(coords, (list, tuple))
        and len(coords) >= 2
        and all(isinstance(v, (int, float)) for v in coords[:2])
    ):
        yield float(coords[0]), float(coords[1])
        return
    if isinstance(coords, (list, tuple)):
        for part in coords:
            yield from _flatten_coords(part)


def geometry_center(geometry: dict[str, Any]) -> Optional[tuple[float, float]]:
    """(lat, lng): the point itself, else the mean of all vertices (as the app)."""
    pts = list(_flatten_coords(geometry.get("coordinates")))
    if not pts:
        return None
    lng = sum(p[0] for p in pts) / len(pts)
    lat = sum(p[1] for p in pts) / len(pts)
    return lat, lng


def chargers_from_geojsonseq(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    """osmium export -f geojsonseq -u type_id (record separators allowed)."""
    for line in lines:
        line = line.strip().lstrip("\x1e").strip()
        if not line:
            continue
        try:
            feature = json.loads(line)
        except json.JSONDecodeError:
            continue
        props = feature.get("properties") or {}
        tags = {str(k): str(v) for k, v in props.items() if not str(k).startswith("@")}
        if tags.get("amenity") != "charging_station" or not is_public(tags):
            continue
        raw_id = str(feature.get("id") or props.get("@id") or "")
        match = re.fullmatch(r"([nwr])(\d+)", raw_id)
        if match is None:
            continue
        center = geometry_center(feature.get("geometry") or {})
        if center is None:
            continue
        yield charger_json(_TYPE_PREFIX[match.group(1)], match.group(2), center[0], center[1], tags)


def chargers_from_overpass(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for el in data.get("elements") or []:
        tags = {str(k): str(v) for k, v in (el.get("tags") or {}).items()}
        if tags.get("amenity") != "charging_station" or not is_public(tags):
            continue
        osm_type, osm_id = el.get("type"), el.get("id")
        point = el.get("center") or el
        lat, lng = point.get("lat"), point.get("lon")
        if not osm_type or osm_id is None or lat is None or lng is None:
            continue
        yield charger_json(str(osm_type), osm_id, float(lat), float(lng), tags)


# ---- output -----------------------------------------------------------------


def bucket(chargers: Iterable[dict[str, Any]]) -> dict[tuple[int, int], list[dict[str, Any]]]:
    cells: dict[tuple[int, int], dict[str, dict[str, Any]]] = {}
    for c in chargers:
        cells.setdefault(cell_of(c["lat"], c["lng"]), {})[c["id"]] = c
    return {k: sorted(v.values(), key=lambda c: c["id"]) for k, v in cells.items()}


def parse_region(spec: str) -> dict[str, Any]:
    name, _, box = spec.partition(":")
    parts = [float(v) for v in box.split(",")]
    if not name or len(parts) != 4:
        raise ValueError(f"region must be ID:S,W,N,E, got {spec!r}")
    s, w, n, e = parts
    if not (s < n and w < e):
        raise ValueError(f"empty region bbox {spec!r}")
    return {"id": name, "bbox": [s, w, n, e]}


def write_tiles(
    out_dir: Path,
    cells: dict[tuple[int, int], list[dict[str, Any]]],
    regions: list[dict[str, Any]],
    *,
    built_at: Optional[float] = None,
) -> dict[str, Any]:
    """Writes v1/ next to a v1.tmp/ and swaps, so a host never serves half a build."""
    root = out_dir / f"v{FORMAT_VERSION}"
    tmp = out_dir / f"v{FORMAT_VERSION}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    for (x, y), rows in cells.items():
        path = tmp / "cells" / str(x) / f"{y}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), "utf-8")
    index = {
        "v": FORMAT_VERSION,
        "built": int(built_at if built_at is not None else time.time()),
        "cell": [CELL_LAT, CELL_LNG],
        "count": sum(len(r) for r in cells.values()),
        "cells": len(cells),
        "regions": regions,
        "attribution": "© OpenStreetMap contributors (ODbL)",
    }
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "index.json").write_text(json.dumps(index, separators=(",", ":")), "utf-8")
    if root.exists():
        shutil.rmtree(root)
    tmp.rename(root)
    return index


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--geojsonseq", action="append", default=[], type=Path)
    ap.add_argument("--overpass-json", action="append", default=[], type=Path)
    ap.add_argument("--region", action="append", default=[], help="ID:S,W,N,E")
    ap.add_argument(
        "--regions-file",
        type=Path,
        help="lines 'ID S W N E' (written by the workflow per extract)",
    )
    ap.add_argument(
        "--min-count",
        type=int,
        default=0,
        help="refuse to write fewer chargers: a broken build must not replace good tiles",
    )
    args = ap.parse_args(argv)

    regions = [parse_region(r) for r in args.region]
    if args.regions_file:
        for line in args.regions_file.read_text("utf-8").splitlines():
            parts = line.split()
            if len(parts) == 5:
                regions.append(parse_region(f"{parts[0]}:{','.join(parts[1:])}"))
    if not regions:
        ap.error("at least one --region: without coverage the app cannot trust a 404")

    def all_chargers() -> Iterator[dict[str, Any]]:
        for path in args.geojsonseq:
            with path.open("r", encoding="utf-8") as fh:
                yield from chargers_from_geojsonseq(fh)
        for path in args.overpass_json:
            yield from chargers_from_overpass(json.loads(path.read_text("utf-8")))

    cells = bucket(all_chargers())
    total = sum(len(r) for r in cells.values())
    if total < args.min_count:
        print(f"only {total} chargers, expected at least {args.min_count}; nothing written", file=sys.stderr)
        return 1
    index = write_tiles(args.out, cells, regions)
    print(f"{index['count']} chargers in {index['cells']} cells, {len(regions)} regions → {args.out}/v1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
