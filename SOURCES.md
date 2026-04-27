# Data sources

This repository combines four public Maryland datasets to answer the question
"which ENOUGH grantees are good candidates to apply for a Judy Center?".

## ENOUGH grantee → census tract roster

`2025 Updated ENOUGH Grantee Census Tracts and Zipcodes - Unique ENOUGH Census Tracts.csv`
Provided by the Maryland Governor's Office for Children (GOC). Contains the 28
ENOUGH grantees and the 106 unique census tracts they collectively serve. The
GOC supplied the same roster to MDP for the public ENOUGH map.

## Judy Center master list

`judy_centers_full.csv` is reconciled from three public Maryland State
Department of Education sources:

1. **December 2025 MSDE Judy Center Coordinator Contact List**
   (`judycentercoordinatorlistwebsitefeb2015_.pdf`, last updated 12/9/2025) —
   distributed at
   `https://earlychildhood.marylandpublicschools.org/`. Authoritative list of
   sites and the per-site coordinator point-of-contact.
2. **2019 MSDE Judy Center Coordinator Contact List** (same URL, earlier
   vintage). Reproduced locally in `internal/` (gitignored) for the data
   reconciliation step but not republished in this public repo — the
   markdown carried direct phone numbers and email addresses that, while
   public-record, are better not amplified through GitHub indexing. Refer
   to MSDE's PDF as the authoritative source.
3. **LEA-published Judy Center pages and FY24/25 board documents** for sites
   added between the two PDFs (FCPS, MCPS, HCPS, BayNet 2025 reporting).

Coordinator names that appear in `judy_centers_full.csv` are reproduced from
MSDE's publicly distributed coordinator PDFs and represent each Judy Center's
single point-of-contact at the host school. They are not personal contact
details. See `judy_center_contact_list_2019.md` for the full extracted PDF.

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
