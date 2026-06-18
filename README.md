# ENOUGH × Judy & Patty Center coverage analysis

Maryland's 28 [ENOUGH](https://goc.maryland.gov/enough/) grantees serve 106
census tracts. The state has 92 [Judy Center Early Learning
Hubs](https://earlychildhood.marylandpublicschools.org/families/judy-centers)
and 17 Patty Centers (Family Support Centers). This tool sorts the grantees
into three groups — those that already have a Judy Center in their served
tracts, those served by a nearby Judy Center whose attendance zone reaches
in, and those with no Judy Center coverage at all — and lists the elementary
schools that could host a Judy Center for grantees in the third group. It
also maps every Patty Center and flags which ENOUGH grantees do not have one
nearby (a Patty Center inside a served tract, within 5 km, or neither).

## Dashboard

| | |
|---|---:|
| ENOUGH grantees | **28** |
| &nbsp; — Already covered (Judy Center in served tract) | 15 |
| &nbsp; — Served by a nearby Judy Center | 8 |
| &nbsp; — **Not served by a Judy Center** | **5** |
| Judy Centers statewide | 92 |
| &nbsp; — In ENOUGH grantee tracts | 17 |
| &nbsp; — Reaching grantee tracts via attendance zone only | 12 |
| Distinct candidate elementary schools in grantee tracts | 35 |

The five grantees not served by a Judy Center — the primary candidates for a
new Judy Center — are CASA Inc., Good Intentions Foundation, Latin American
Youth Center, Leaders Breeders Inc., and Urban Strategies Inc.

## Patty Centers

Patty Centers (formerly Family Support Centers) are community-based hubs for
parents of children birth–3, run by Maryland Family Network as MSDE's
intermediary — not housed in schools, so they have no attendance zone. Each
ENOUGH grantee is tiered by Patty Center proximity: **3** have one inside a
served tract, **15** have one within 5 km, and **10** have none nearby. The
Blueprint funds 3 new Patty Centers per year through FY2030 at $330,000 each,
so the 10 grantees with no nearby center are natural expansion candidates.
See `patty_center_coverage.csv` and the "Patty Center coverage" section of
`executive_summary.md`.

## Where the analysis lives

- **`executive_summary.md`** — stakeholder-facing narrative with the
  dashboard, the 3-category breakdown, and per-grantee candidate detail.
- **Interactive site (`site/`)** — tabbed Leaflet app: **Overview**
  (dashboard), **Map** (color-coded by category), **Grantees** (per-grantee
  detail blocks), **Methodology** (data sources, match tiers, caveats).
  Live URL is published once the repo is hosted under the GOC organization
  on GitHub.
- **`SOURCES.md`** — provenance for every dataset used.

## Needs-Judy-Center detail

The five grantees in the **Needs Judy Center** category, with their top
non-JC candidate elementary by CPG:

| Grantee | County | Top candidate(s) |
|---|---|---|
| United Communities Against Poverty | Prince George's | District Heights Elementary (90%); Frances Scott Key Elem (86%) |
| Urban Strategies Inc | Baltimore City | National Academy Foundation (90%) |
| Latin American Youth Center | Prince George's | Adelphi Elementary (87%); Cherokee Lane Elementary (85%) |
| Good Intentions Foundation Inc. | Prince George's | Glenarden Woods Elementary (41%) |
| Leaders Breeders Inc. | Baltimore City | _closest fallback: Sinclair Lane Elementary at 0.5 km, CPG 91%_ |

The four named-overlap exemplars — **City of Cumberland** (South Penn ES),
**One Annapolis** (Georgetown East ES), **San Mar** (Bester ES),
**Frederick LMB** (Waverley/Hillcrest/Butterfly Ridge ES) — are validated
as already-aligned. **Prince George's County DSS** is also covered:
Hillcrest Heights Elementary (a Judy Center confirmed via MarylandChild.org)
sits inside one of PG DSS's served tracts.

## Pipeline

`analyze.py`:

1. Refreshes `judy_centers_full.csv` from [MarylandChild.org's Judy Center Hubs
   Map](https://marylandchild.org/family-support/judy-center-hubs-map/) (the
   authoritative current list, 92 sites as of April 2026). The same feed run
   also refreshes `patty_centers_full.csv` from the 17 "(PC)"-tagged entries
   (see step 8).
2. Pulls three layers from
   [MDP's `ENOUGH_Act_2025` FeatureServer](https://mdpgis.mdp.state.md.us/arcgis/rest/services/Society/ENOUGH_Act_2025/FeatureServer):
   School Locations 2025 (Layer 0), School Attendance Zones 2025 (Layer 1),
   Poverty Census Tracts 2025 (Layer 2).
3. Reprojects to NAD83 / Maryland State Plane (EPSG:26985).
4. Spatial-joins schools → tracts (`gpd.sjoin(predicate='within')`). All 1,377
   schools resolve.
5. Resolves each Judy Center through four tiers (in order of accuracy):
   exact normalized name match within county → rapidfuzz `token_sort_ratio ≥ 85`
   within county → address-based `token_sort_ratio ≥ 90` within county →
   MarylandChild.org lat/lon point-in-polygon. All 92 JCs resolve.
6. Runs an overlay against Layer 1 catchments to surface "near-miss" Judy
   Centers — sites whose host school is outside a grantee's tracts but whose
   attendance zone overlaps the tracts by ≥ 5%, including the Frederick LMB ↔
   Lincoln ES and South Baltimore CLT ↔ Maree G. Farring cases.
7. Surfaces candidate elementary schools per grantee. Schools are listed in
   CPG-descending order for visual prioritisation; CPG is information, not a
   gate (per GOC guidance). Emits `candidate_elementary_schools.csv`,
   `executive_summary.md`, and the `site/data/*` files the interactive map
   reads.
8. Refreshes `patty_centers_full.csv` from the same MarylandChild.org feed
   (the 17 "(PC)"-tagged entries), resolves each to a tract via its lat/lon,
   and tiers every grantee by Patty Center proximity (in served tract → within
   5 km → none nearby). Emits `patty_center_coverage.csv`, `pc_match_audit.csv`,
   `site/data/patty_centers.geojson`, and the Patty Center columns on
   `grantee_coverage.csv`.

## Run it

```bash
pip install geopandas rapidfuzz
python analyze.py
```

First run downloads ~4 MB of GeoJSON from MDP (cached under `.cache/` for
subsequent runs). Output appears in the project root. Re-run after editing
`judy_centers_full.csv` or the grantee roster to refresh every artefact.

To preview the interactive map locally:

```bash
python -m http.server 8000 --directory site
# open http://localhost:8000/
```

## Outputs

| File | Purpose |
|---|---|
| `executive_summary.md` | Stakeholder narrative + recommendations |
| `grantee_coverage.csv` | One row per grantee with JC coverage flags, near-miss column, and Patty Center tier columns |
| `judy_center_coverage.csv` | One row per Judy Center with resolved tract + grantees served |
| `candidate_elementary_schools.csv` | Elementary schools in grantee tracts, ranked by CPG |
| `jc_match_audit.csv` | Per-Judy-Center match decision audit |
| `patty_centers_full.csv` | Patty Center list refreshed from MarylandChild.org |
| `patty_center_coverage.csv` | One row per Patty Center with resolved tract + grantees served |
| `pc_match_audit.csv` | Per-Patty-Center tract-resolution audit |
| `catchment_overlaps.csv` | Long-format catchment-vs-tract overlap audit |
| `catchment_countywide_flags.csv` | Schools whose catchment is ≥ 70% of county area |
| `gaps.csv` | Grantees with no Judy Center in their county (currently empty) |
| `site/index.html` + `site/data/*` | Interactive Leaflet map (incl. `patty_centers.geojson`) |
| `sanity_overview_md.png`, `sanity_overview_bc.png` | Static overlays for visual verification |

See `SOURCES.md` for data provenance and known vintage gaps.

## Outstanding follow-ups

These don't block the current GOC operational decision:

- **Cross-check the MarylandChild.org list with MSDE's coordinator PDF.**
  All 92 sites in `judy_centers_full.csv` come from
  marylandchild.org as of April 2026. MarylandChild.org is the public-facing
  Judy Center directory MFN maintains for MSDE; spot-checks against the
  coordinator PDF agree, but a periodic round-trip is worth doing.
- **MarylandChild.org lat/lon precision.** Some pins on MFN's map are off by
  enough to land in a different census tract (Georgetown East ES is pinned
  in the Bay; Arlington ES is ~1 km off). The pipeline mitigates by
  preferring MDP's name match for tract assignment, but the underlying MFN
  geocoding is worth cleaning up.
- **ACS vintage.** `CHILD_POV_PCT` doesn't declare its ACS 5-year vintage on
  MDP's tract layer.

If a deploy fails, see [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) for the
seven failure categories and the commands to diagnose each.

Repository owner: Maryland Governor's Office for Children. Analysis pipeline
prepared in collaboration with [Claude Code](https://claude.com/claude-code).
