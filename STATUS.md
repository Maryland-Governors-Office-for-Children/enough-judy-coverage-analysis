# ENOUGH × Judy & Patty Center coverage — Project Status & Open Steps

_Last reviewed: 2026-06-18._ Living index of what's done, what's open, and what needs a
decision. See `README.md` for usage, `SOURCES.md` for data provenance,
`enough_map_data_dictionary.md` for the MDP layer schema, and `TROUBLESHOOTING.md` for the
GitHub Pages deploy diagnostic.

Live site: https://maryland-governors-office-for-children.github.io/enough-judy-coverage-analysis/

## What this is

A static Leaflet site + reproducible Python pipeline (`analyze.py`) that maps Maryland's 28
ENOUGH grantees against 92 Judy Center Early Learning Hubs and 17 Patty Centers, classifies
each grantee by coverage, and (for Judy Centers) ranks candidate elementary schools. Built to
answer Naomi's question on which ENOUGH grantees are Judy Center candidates, and Peter
Willenborg's follow-up (Apr 28 2026) on which ENOUGH grantees lack a Patty Center.

## Workstream A — Judy Center coverage (original build)

**Done**
- Pipeline reproduces deterministically: 28 grantees, 106 tracts, 92 JCs, **15/8/5** split
  (in-tract / catchment-overlap / needs-JC). All 92 JCs resolve (83 exact, 5 fuzzy, 4 lat/lon).
- Tabbed Leaflet site (Overview / Map / Grantees / Methodology) deployed to GitHub Pages.
- Outputs: `executive_summary.md`, `grantee_coverage.csv`, `judy_center_coverage.csv`,
  `candidate_elementary_schools.csv`, `jc_match_audit.csv`, catchment audits, `site/data/*`.

## Workstream B — Patty Center incorporation (this session, 2026-06-18)

**Done**
- Parsed the 17 "(PC)"-tagged Patty Centers from the same MarylandChild.org feed (previously
  excluded). All 17 geocode cleanly; **3** in a grantee tract, **15** within 5 km, **10** none
  nearby. Tiering is straight-line distance (PCs aren't school-based → no attendance zone).
- New outputs: `patty_centers_full.csv`, `patty_center_coverage.csv`, `pc_match_audit.csv`,
  `site/data/patty_centers.geojson`, + 4 Patty columns on `grantee_coverage.csv`.
- `executive_summary.md` gains a "Patty Center coverage" section answering Peter's question
  directly (which grantees have none nearby), with the FY2030 3/year @ $330k expansion note.
- Map: teal-square PC layer with sidebar toggle; per-grantee emphasis on select; dashboard
  tiles; Overview + Methodology copy; site retitled "Judy & Patty Center coverage."
- Plain `&amp;`/`&#39;` HTML entities in feed names now unescaped (`html.unescape`).

**Open (in-repo, not blocking)**
- [ ] Revisit the 5 km "nearby" threshold against the live map — may be tight in rural counties.
- [ ] Cross-check the 17 PCs against MSDE's count (spreadsheet row 3 cites 35 statewide in 2024;
      the public MFN map shows 17 — confirm whether MFN lists a subset).

## Open — needs Nick (decision / external)

- [ ] **Push to GitHub.** Backend+frontend changes are committed locally only. Pushing
      republishes the public site — confirm before first push (per GOC convention).
- [ ] Decide whether to fold in the MDMOM resource map / New-Parents-guide resources Peter and
      Rachel raised in the same thread (separate, larger scope).
- [ ] Share the updated link with Peter/Rachel once pushed.

## Next steps / potential uses

Ideas for where this map could go next (none built yet — captured here so they're not lost):

- **Patty Center expansion prioritization (FY2030 grant).** The Blueprint funds 3 new Patty
  Centers/year through FY2030 at $330k each. Rank the 10 "no nearby PC" grantees into a
  `pc_expansion_priority.csv` using child-poverty rate, distance to the nearest existing PC,
  and county PC count — a direct decision aid for Peter's question. (Mirror of the JC
  candidate-elementary logic, adapted: PCs aren't school-sited, so rank communities, not schools.)
- **Cross-link the New-Parents guide / MDMOM resource map.** Peter & Rachel raised
  maryland.gov/new-parents and the Hopkins/MDH map (mdmomresources.org/map) in the same thread.
  Option: a combined 0–5 resource view, or simply linking out from the Methodology tab. Larger
  scope — scope it with Peter before building.
- **Threshold tuning.** Revisit the 5 km PC "nearby" cutoff against the live map; it may be tight
  in rural counties (e.g. Eastern Shore) and generous in dense Baltimore/PG. `PC_NEARBY_M` is a
  single constant in `analyze.py`; document any change in SOURCES.
- **Periodic data refresh + MFN-vs-MSDE reconcile.** Re-run the pipeline when MFN updates the
  feed; cross-check the 17 PCs / 92 JCs against MSDE's coordinator PDF (the spreadsheet cites 35
  PCs statewide in 2024 vs 17 on the public map — confirm whether MFN lists a subset).
- **Share the live link with Peter & Rachel** once pushed (answers Peter's Apr 28 ask directly).

## Decisions made

- **Scope:** lightweight PC overlay + per-grantee flag (no PC candidate-site ranking) — honors
  Peter's "don't want to expand scope too much."
- **Coverage model:** tiered by distance (in-tract → ≤5 km → none), `PC_NEARBY_M = 5000`.
- **Source of truth:** MarylandChild.org SSF feed for both JC and PC locations; MDP layers for
  schools/tracts/catchments. No data drift since April 2026 (verified byte-identical).

## Environment

`python analyze.py` in `.venv` (geopandas 1.1.3, rapidfuzz). First run caches ~4 MB MDP GeoJSON
+ the MFN feed under `.cache/` (gitignored). `python -m http.server --directory site` to preview.
