#!/usr/bin/env python3
"""List Geofabrik extracts under a parent and the bboxes they cover.

Reads https://download.geofabrik.de/index-v1.json (with geometry).

  python3 geofabrik_regions.py index-v1.json europe --list    → "id pbf_url" per extract
  python3 geofabrik_regions.py index-v1.json europe --bboxes  → "id S W N E" per polygon

One bbox per polygon part, so Spain's Canary Islands do not make the whole
of Morocco look covered. Only direct children of the parent are listed:
those are whole countries, which the tile build needs anyway. Bundles
such as "dach" that repeat other children are skipped.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Iterator


def _rings(geometry: dict[str, Any]) -> Iterator[list[list[float]]]:
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "Polygon":
        if coords:
            yield coords[0]
    elif kind == "MultiPolygon":
        for poly in coords:
            if poly:
                yield poly[0]


# Bundles of countries that are also listed one by one: downloading them
# again would only cost time and disk.
COMPOSITES = frozenset({"alps", "britain-and-ireland", "dach", "united-kingdom"})


def children(index: dict[str, Any], parent: str) -> list[dict[str, Any]]:
    return [
        f
        for f in index.get("features") or []
        if (f.get("properties") or {}).get("parent") == parent
        and (f.get("properties") or {}).get("id") not in COMPOSITES
        and ((f.get("properties") or {}).get("urls") or {}).get("pbf")
    ]


def bboxes(feature: dict[str, Any]) -> list[tuple[float, float, float, float]]:
    out = []
    for ring in _rings(feature.get("geometry") or {}):
        lngs = [p[0] for p in ring]
        lats = [p[1] for p in ring]
        out.append((round(min(lats), 4), round(min(lngs), 4), round(max(lats), 4), round(max(lngs), 4)))
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[2] not in ("--list", "--bboxes"):
        print(__doc__)
        return 2
    with open(argv[0], encoding="utf-8") as fh:
        index = json.load(fh)
    for f in children(index, argv[1]):
        props = f["properties"]
        if argv[2] == "--list":
            print(props["id"], props["urls"]["pbf"])
        else:
            for s, w, n, e in bboxes(f):
                print(props["id"], s, w, n, e)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
