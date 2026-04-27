# ENOUGH × Judy Center coverage analysis

Cross-reference of Maryland's 28 [ENOUGH](https://goc.maryland.gov/enough/)
grantees against the state's 79 [Judy Center Early Learning
Hubs](https://earlychildhood.marylandpublicschools.org/families/judy-centers).
Answers the operational question: **which ENOUGH grantees are good candidates
to apply for a Judy Center?**

A grantee is a strong candidate when its served census tracts contain at least
one high-poverty elementary school but no Judy Center is currently sited
there. Of the 28 grantees, **14 already have a Judy Center inside one of their
served tracts** and **14 do not**. The 14 without coverage are the focus.

- **`executive_summary.md`** — stakeholder-facing narrative, ranked candidate
  list per grantee, methodology, caveats.
- **Interactive map** — pick a grantee, see their tract polygons shaded, the
  Judy Centers and candidate elementary schools on the map, and the
  recommendation in the sidebar.
  Live site URL is published at the top of this README once the repo is
  hosted under the GOC organization on GitHub.
- **`SOURCES.md`** — provenance for every dataset used.

## Headline candidates

The 14 grantees without tract-level Judy Center coverage, ranked by the
concentrated-poverty rate (CHILD_CPG_PCT) of the highest-CPG candidate
elementary school in their tracts:

| Grantee | County | Top non-JC candidate (CPG) |
|---|---|---|
| Elev8 Baltimore | Baltimore City | Sandtown-Winchester Achievement Academy (96%) |
| Greater Mondawmin Coordinating Council | Baltimore City | Robert W Coleman Elementary (96%) |
| CASA, Inc. | Prince George's | Langley Park/McCormick Elem (95%) |
| The Central Baltimore Partnership | Baltimore City | Cecil Elementary (92%) |
| The Y in Central Maryland | Baltimore City | Walter P Carter Elementary/Middle (92%) |
| Tendea Family Inc. | Baltimore City | Tench Tilghman Elementary/Middle (91%) |
| United Communities Against Poverty | Prince George's | District Heights Elementary (90%) |
| Urban Strategies Inc. | Baltimore City | National Academy Foundation (90%) |
| Prince George's County DSS | Prince George's | Hillcrest Heights Elementary (89%) |
| Latin American Youth Center | Prince George's | Adelphi Elementary (87%) |
| Identity, Inc. | Montgomery | South Lake Elementary (85%) |
| Community Assistance Network | Baltimore County | Deep Creek Elementary (81%) |
| Good Intentions Foundation | Prince George's | Glenarden Woods Elementary (41%) |
| Leaders Breeders Inc. | Baltimore City | _no traditional elementary in tract — only Belair-Edison Charter_ |

The four grantees whose names already reference their host Judy Center —
**City of Cumberland** (South Penn ES), **One Annapolis** (Georgetown East
ES), **San Mar** (Bester ES), **Frederick LMB** (3 of 6 FCPS Judy Centers) —
are validated as already-aligned exemplars.

## Pipeline

`analyze.py`:

1. Pulls three layers from
   [MDP's `ENOUGH_Act_2025` FeatureServer](https://mdpgis.mdp.state.md.us/arcgis/rest/services/Society/ENOUGH_Act_2025/FeatureServer):
   School Locations 2025 (Layer 0), School Attendance Zones 2025 (Layer 1),
   Poverty Census Tracts 2025 (Layer 2).
2. Reprojects to NAD83 / Maryland State Plane (EPSG:26985).
3. Spatial-joins schools → tracts (`gpd.sjoin(predicate='within')`). All 1,377
   schools resolve.
4. Resolves each Judy Center to an MDP school point through three tiers:
   exact normalized name within county → rapidfuzz `token_sort_ratio ≥ 85`
   within county → address-based fallback. 74 of 79 sites resolve cleanly;
   5 are external data-quality flags listed in the executive summary.
5. Runs an overlay against Layer 1 catchments to surface "near-miss" Judy
   Centers — sites whose host school is outside a grantee's tracts but whose
   attendance zone overlaps the tracts by ≥ 5%. Ten such overlaps surface,
   including the Frederick LMB ↔ Lincoln ES and South Baltimore CLT ↔ Maree
   G. Farring cases.
6. Ranks candidate elementary schools per grantee by CHILD_CPG_PCT and emits
   `candidate_elementary_schools.csv`, `executive_summary.md`, and the
   `site/data/*` files the interactive map reads.

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

These don't block the current GOC operational decision but should round-trip
through MSDE / MDP at some point:

- **Five unresolved Judy Centers.** Eutaw-Marshburn ES (Baltimore City; likely
  closure), three names from the December 2025 PG County PDF excerpts, and
  Kent's Henry Highland Garnett (MDP records it as "H H Garnett"). All listed
  in `executive_summary.md` and `jc_match_audit.csv`.
- **CPG threshold.** MDP's 2025 documentation references a 75% CPG threshold;
  the underlying field metadata still says 80%. Worth confirming which the
  values reflect.
- **ACS vintage.** `CHILD_POV_PCT` doesn't declare its ACS 5-year vintage on
  the layer.
- **Recommendation thresholds (≥85% strong, ≥65% possible).** Heuristic; would
  ideally be calibrated to a GOC/MSDE policy number rather than this code.

Repository owner: Maryland Governor's Office for Children. Analysis pipeline
prepared in collaboration with [Claude Code](https://claude.com/claude-code).
