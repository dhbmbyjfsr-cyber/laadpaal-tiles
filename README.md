# laadpaal-tiles

Static JSON tiles of public EV charging stations from OpenStreetMap, rebuilt
weekly from Geofabrik extracts and served with GitHub Pages. Used by the
Laadpaalprijzen app outside the Netherlands and France.

## Format (v1)

Grid: 0.12° latitude × 0.18° longitude, `x = floor(lng / 0.18)`,
`y = floor(lat / 0.12)`.

```
{base}/v1/index.json           build info and covered regions
{base}/v1/cells/{x}/{y}.json   chargers in that cell
```

A 404 for a cell inside a covered region means no public chargers there.

## Build

`.github/workflows/charger-tiles.yml` runs every Monday and on demand
(Actions → charger-tiles → Run workflow). It downloads each region, keeps
`amenity=charging_station`, and refuses to publish a build below
`min_count` chargers, so a failed build leaves the previous one online.

## License

Data © OpenStreetMap contributors, available under the
[Open Database License (ODbL)](https://www.openstreetmap.org/copyright).
