# ENOUGH × Judy Center coverage analysis

Cross-reference of Maryland's 28 [ENOUGH](https://goc.maryland.gov/enough/)
grantees against the state's 92 [Judy Center Early Learning
Hubs](https://earlychildhood.marylandpublicschools.org/families/judy-centers).
Answers the operational question: **which ENOUGH grantees are candidates to
apply for a Judy Center?**

Per GOC guidance, the operational filter is presence of an elementary school
in a JC-free grantee tract — not a CPG (concentrated poverty grant) threshold.
CPG is shown alongside each candidate as information so reviewers can
prioritise where to start, not as a gate.

## Dashboard

| | |
|---|---:|
| ENOUGH grantees | **28** |
| &nbsp; — Already covered (Judy Center in served tract) | 15 |
| &nbsp; — Catchment overlap (no in-tract JC, near-miss catchment) | 8 |
| &nbsp; — **Needs Judy Center** | **5** |
| Judy Centers statewide | 92 |
| &nbsp; — In ENOUGH grantee tracts | 17 |
| &nbsp; — Catchment-overlap only | 12 |
| Distinct prospective elementaries in grantee tracts | 35 |

- **Already covered (15)** — at least one Judy Center inside a served census
  tract. No new application needed.
- **Catchment overlap (8)** — no in-tract Judy Center, but a JC's school
  attendance zone overlaps a served tract by ≥ 5%. Coordination opportunity;
  may not require a new application.
- **Needs Judy Center (5)** — neither in-tract nor catchment overlap.
  Primary candidates for new Judy Center applications: CASA Inc., Good
  Intentions Foundation, Latin American Youth Center, Leaders Breeders Inc.,
  Urban Strategies Inc.

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
   authoritative current list, 92 sites as of April 2026).
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
| `grantee_coverage.csv` | One row per grantee with coverage flags + near-miss column |
| `judy_center_coverage.csv` | One row per Judy Center with resolved tract + grantees served |
| `candidate_elementary_schools.csv` | Elementary schools in grantee tracts, ranked by CPG |
| `jc_match_audit.csv` | Per-Judy-Center match decision audit |
| `catchment_overlaps.csv` | Long-format catchment-vs-tract overlap audit |
| `catchment_countywide_flags.csv` | Schools whose catchment is ≥ 70% of county area |
| `gaps.csv` | Grantees with no Judy Center in their county (currently empty) |
| `site/index.html` + `site/data/*` | Interactive Leaflet map |
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

Repository owner: Maryland Governor's Office for Children. Analysis pipeline
prepared in collaboration with [Claude Code](https://claude.com/claude-code).
