# Data sources

This repository combines four public Maryland datasets to answer the question
"which ENOUGH grantees are good candidates to apply for a Judy Center?".

## ENOUGH grantee → census tract roster

`2025 Updated ENOUGH Grantee Census Tracts and Zipcodes - Unique ENOUGH Census Tracts.csv`
Provided by the Maryland Governor's Office for Children (GOC). Contains the 28
ENOUGH grantees and the 106 unique census tracts they collectively serve. The
GOC supplied the same roster to MDP for the public ENOUGH map.

## Judy Center master list

`judy_centers_full.csv` is regenerated on every run of `analyze.py` from the
**Maryland Family Network's Judy Center Hubs Map** at
`https://marylandchild.org/family-support/judy-center-hubs-map/`. The map is
backed by a SuperStoreFinder XML feed at
`https://marylandchild.org/wp-content/plugins/superstorefinder-wp/ssf-wp-xml.php`,
which the pipeline caches locally to `.cache/marylandchild_ssf.xml` on first
run. As of April 2026 the feed lists 92 Judy Centers (and 17 Patty Family
Support Centers, which the pipeline excludes via the "(JC)" / "(PC)" suffix
on each item's location field).

Each item carries: school name, street address, city, ZIP, latitude,
longitude, county tag, telephone, and an internal MFN store ID. The pipeline
records all of these except telephone (not needed for the spatial analysis;
GOC can pull it from MFN's site or MSDE's coordinator PDF when needed).

MFN's map is the public-facing Judy Center directory maintained for MSDE.
For the most current authoritative source -- including the per-site
coordinator point-of-contact -- consult MSDE's December 2025 Judy Center
Coordinator PDF
(`https://earlychildhood.marylandpublicschools.org/`). The pipeline does not
ingest the PDF directly; it was used in earlier iterations as a
cross-reference and is preserved (without phone/email PII) in `internal/`
locally.

## Maryland Department of Planning ENOUGH layers

The site reads three layers from MDP's `ENOUGH_Act_2025` FeatureServer:

| Layer | URL | Purpose |
|---|---|---|
| 0 — School Locations 2025 | `…/FeatureServer/0` | School points + CHILD_CPG_PCT |
| 1 — School Attendance Zones 2025 | `…/FeatureServer/1` | Catchment polygons |
| 2 — Poverty Census Tracts 2025 | `…/FeatureServer/2` | Tract polygons + child poverty |

Base URL: `https://mdpgis.mdp.state.md.us/arcgis/rest/services/Society/ENOUGH_Act_2025/FeatureServer`.
The schema is documented in `enough_map_data_dictionary.md`. Layers were
downloaded April 2026 and cached locally under `.cache/` (gitignored).

## Vintages and known gaps

- `CHILD_CPG_PCT` (Layer 0) is described by MDP as a three-year FARMS rolling
  average. The exact year span is not declared on the layer.
- `CHILD_POV_PCT` (Layer 2) is sourced from the ACS 5-year survey. The exact
  vintage (2018-2022 vs 2019-2023) is not declared on the layer; likely
  2019-2023, the most recent release as of December 2024.
- Five Judy Centers in `judy_centers_full.csv` could not be resolved against
  MDP Layer 0 (one likely closure, three Prince George's names from Dec 2025
  PDF excerpts pending MSDE confirmation, one Kent County school whose initials
  form differs from the PDF's full name). See `jc_match_audit.csv` for details.
- The FY24 MSDE board document references 86 Judy Center sites; this analysis
  uses 79. The 7-row gap is most likely Cecil County's post-2019 expansion
  (1 → 10 sites; non-ENOUGH county) plus pending Prince George's additions.

## Reproducibility

`python analyze.py` regenerates every CSV, the executive summary, and the
`site/data/*` files used by the interactive map. MDP layers cache to `.cache/`
on first run; subsequent runs are seconds.
