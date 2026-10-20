#!/usr/bin/env python3
"""amenity=charging_station from an .osm.pbf → geojsonseq, without osmium-tool.

One GeoJSON feature per line, id "n123" or "w123", tags as properties: what
build_charger_tiles.py reads. A way gets its vertices as a LineString, and
the builder takes their mean, like the app does for QLever polygons.
Needs `pip install osmium` (pyosmium 3.7+). In CI, run
`osmium tags-filter in.pbf nwr/amenity=charging_station -o f.pbf` first:
the C++ filter is far faster than reading a whole country here.

Usage: python3 pbf_chargers_geojsonseq.py in.osm.pbf out.geojsonseq
"""

from __future__ import annotations

import json
import sys

import osmium  # type: ignore


def main(src: str, dst: str) -> int:
    fp = (
        osmium.FileProcessor(src)
        .with_locations()
        .with_filter(osmium.filter.KeyFilter("amenity"))
    )
    n = 0
    with open(dst, "w", encoding="utf-8") as out:
        for obj in fp:
            tags = {t.k: t.v for t in obj.tags}
            if tags.get("amenity") != "charging_station":
                continue
            if obj.is_node():
                if not obj.location.valid():
                    continue
                geom = {"type": "Point", "coordinates": [obj.location.lon, obj.location.lat]}
                fid = f"n{obj.id}"
            elif obj.is_way():
                coords = [[nd.lon, nd.lat] for nd in obj.nodes if nd.location.valid()]
                if not coords:
                    continue
                geom = {"type": "LineString", "coordinates": coords}
                fid = f"w{obj.id}"
            else:
                continue  # charging_station relations are almost unheard of
            out.write(json.dumps({"type": "Feature", "id": fid, "geometry": geom, "properties": tags}))
            out.write("\n")
            n += 1
    print(f"{n} charging stations → {dst}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
