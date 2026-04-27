# ENOUGH × Judy Center Prototype — Summary

## What this is

A first-pass coverage analysis cross-referencing the 28 ENOUGH grantees
against Maryland's 79 Judy Center Early Learning Hubs. The tract-level
spatial join is staged but not executed (see "Tract-level join" below);
the analysis here is at the **county level** with a small **name-overlap
heuristic** that flags the cases where the connection is essentially
already named in the grantee.

## Files in this directory

| File | What it is |
|---|---|
| `judy_centers_full.csv` | Reconciled Judy Center master list — every site I could identify across Maryland (79 rows, 24 counties), each tagged with vintage and source |
| `analyze.py` | The pipeline. Reads grantee CSV + judy_centers_full.csv → produces the three CSVs below. Tract-level join is a one-line drop-in (see TODO inside) |
| `grantee_coverage.csv` | One row per ENOUGH grantee. Columns: tracts they serve, county, # Judy Centers in that county, the JC names, and a name-overlap hint |
| `judy_center_coverage.csv` | One row per Judy Center. Columns: county, address, vintage, # ENOUGH grantees in same county, those grantee names |
| `gaps.csv` | Empty by design — every Maryland county has ≥1 Judy Center, so no grantee lacks county-level coverage. The interesting gaps will appear at the *tract* level |
| `enough_map_data_dictionary.md` | (From earlier turn) Full data dictionary for the MDP `ENOUGH_Act_2025` REST service |
| `judy_centers_enough_counties.csv` | (From earlier turn) Earlier, narrower extract — superseded by `judy_centers_full.csv` |

## Headline findings at the county level

- **All 28 grantees have county-level Judy Center coverage.** Every county where
  an ENOUGH grantee operates has at least one Judy Center. This is consistent
  with MSDE's stated policy (every Maryland county has ≥1 Judy Center) — but
  county-level coverage doesn't tell us whether the JC is in the *same
  neighborhood* the grantee is targeting.

- **Four grantees have a strong name-based signal of an already-coordinated
  Judy Center.** These are the most likely to come back as confirmed
  tract-level matches when the spatial join runs:
  - `City of Cumberland` ↔ South Penn Elementary (Cumberland, 21502)
  - `One Annapolis, Inc` ↔ Georgetown East Elementary (Annapolis, 21403)
  - `San Mar Family & Community Services: Bester Community of Hope` ↔ **Bester Elementary School** (Hagerstown, 21740) — the grantee initiative is literally named after the school
  - `Frederick Local Management Board` ↔ all 6 FCPS Judy Centers (Waverley, Lincoln, Butterfly Ridge, Hillcrest, Monocacy, Tuscarora)

- **Baltimore City needs the most attention.** 11 of the 28 grantees serve
  Baltimore City tracts, and BC has 14 Judy Centers. With only county-level
  data, every BC grantee shows "14 JCs in their county" — useful as a sanity
  check (none of them is a JC desert at the county level) but not
  actionable. The tract-level join is what will make this output meaningful
  for BC. The 11 BC grantees collectively cover 56 tracts, so for any given
  JC we'll get a clean answer about which (if any) BC grantee it lands in.

- **Two PG-County name puzzles still unresolved.** The Dec 2025 PDF excerpts
  surfaced "Carlotta G. Robinson-Jackson Elementary" but the name was
  truncated in the search snippet (showed as "Carlotta G. Robinson Jack").
  Per the 2019 PDF, "Carlotta G. Robinson Jack" is the *coordinator's name*
  in Worcester County — not a school. So the 2025 PG County entry might be a
  data extraction artifact rather than a real new school. Worth confirming
  with MSDE.

## Tract-level join — what's missing

The columns `n_jc_in_grantee_tract_TRACT_LEVEL` and
`grantees_serving_this_tract_TRACT_LEVEL` are populated by a function called
`spatial_join_judy_centers_to_tracts()` inside `analyze.py`. It currently
returns `None` for every Judy Center because the runtime environment can't
reach the MDP REST service.

Inside `analyze.py` there's a `TODO` block showing the exact replacement —
about 15 lines that:
1. Read MDP's School Locations 2025 layer as GeoJSON via `geopandas.read_file`
2. Read MDP's Poverty Census Tracts 2025 layer the same way
3. Reproject both to EPSG:26985 (MD State Plane, the layer's native CRS)
4. `gpd.sjoin(..., predicate='within')` to assign each school its tract
5. Look up each Judy Center school in the joined frame by name

When you run this on your machine (or in Claude Code with internet access),
both layer pulls are small (~1400 records each, well under each layer's
2000-record max), so the whole join completes in a few seconds and produces
a real value in those `_TRACT_LEVEL` columns.

## Data quality flags worth knowing

- **2019 vs 2025 vintage.** 60 of the 79 Judy Center rows come from the
  authoritative 2019 MSDE PDF you uploaded. 19 rows are post-2019 additions
  extracted from Dec 2025 PDF search snippets and individual LEA pages.
  Where I have both vintages confirming the same site, I marked it
  `2019 PDF + Dec 2025 PDF`.

- **Likely closures / replacements.** A few 2019 sites don't show in the
  2025 sources: Beall ES (Allegany), Hilltop ES (Anne Arundel), C. Paul
  Barnhart and J.P. Ryon (Charles), Sandy Hill (Dorchester). These could be
  closures, name changes, or just absences from the partial 2025 snippets I
  could extract. For your immediate analysis it doesn't matter much because
  *both* the 2019 and 2025 entries are flagged as JC sites — the analysis
  just slightly over-counts coverage if anything.

- **FY24 board document says 86 JC sites.** My consolidated list has 79
  rows. The 7-row gap is probably the Cecil County expansion (1 site in
  2019 → 10 by FY24) plus a couple of Prince George's additions. Cecil
  isn't an ENOUGH county so its expansion doesn't affect the analysis;
  the PG gap could matter but I have 5 rows for PG already (matching FY24
  total).

## Recommended next steps

1. **Run the spatial join.** This converts every "n_jc_in_counties: 14" row
   for Baltimore City into a real "this grantee has X Judy Centers in their
   actual tracts." That's the analysis output your team needs.
2. **Confirm with MSDE which Judy Centers are currently active.** The 2019
   vs 2025 discrepancies on individual sites are minor but worth nailing
   down before any communications with grantees.
3. **For the Bester / Cumberland / Annapolis hints**, consider direct
   conversations with those four grantees first — those are the easy
   "already-aligned" examples that demonstrate the value of the broader
   analysis.
