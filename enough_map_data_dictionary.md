# MDP ENOUGH Map — Data Dictionary

Reference for the GOC Judy Center / ENOUGH grantee analysis. Captured April 2026.

## Service inventory

MDP publishes the ENOUGH map layers under `mdpgis.mdp.state.md.us` as ArcGIS REST services. There are now **two parallel services**:

| Service | URL | Status |
|---|---|---|
| `ENOUGH_Act` (legacy) | `https://mdpgis.mdp.state.md.us/arcgis/rest/services/Society/ENOUGH_Act/MapServer` (also has `/FeatureServer`) | Powered the original (2024-25) ENOUGH map. Read-mostly. Last metadata refresh July 2024. |
| `ENOUGH_Act_2025` (active) | `https://mdpgis.mdp.state.md.us/arcgis/rest/services/Society/ENOUGH_Act_2025/FeatureServer` | Currently being edited by MDP. Capabilities include Create/Update/Delete. This is the working layer for the 2026 cycle. |

**Use `ENOUGH_Act_2025/FeatureServer` for the analysis.** The legacy service is useful for historical comparison but is stale.

The Experience Builder app at the URL the GOC was given displays a subset of these layers; the REST endpoints expose all of them whether or not they're toggled on in the UI.

## ENOUGH_Act_2025 — Layer inventory

| ID | Name | Geometry | Purpose |
|---|---|---|---|
| 0 | School Locations 2025 | Point | Maryland public school points (use for point-in-tract analysis) |
| 1 | School Attendance Zones 2025 | Polygon | School catchment polygons (for catchment-based / proximity follow-up) |
| 2 | Poverty Census Tracts 2025 | Polygon | Census tracts with child poverty + school-intersection summaries |
| 3 | School Statistics 2025 | Table (no geometry) | Additional school-level attributes (schema not yet retrieved) |

Notably absent from this service: an "ENOUGH Grantees" or "Served Tracts" layer. The grantee → tract mapping must come from the GOC's internal roster (which per the user's note is what GOC supplied to MDP in the first place). Worth confirming whether MDP maintains a grantee layer in the legacy service or in a separate, possibly access-controlled, FeatureServer.

## Layer 0 — School Locations 2025 (points)

Geometry: `esriGeometryPoint`. Spatial reference: Web Mercator (EPSG:3857) on the wire, but underlying source is NAD83 / Maryland State Plane (EPSG:26985).

| Field | Alias | Type | Notes |
|---|---|---|---|
| OBJECTID | OBJECTID | OID | Internal |
| LEA_SITE | Unique School Identifier | String(11) | Maryland LEA + site code. Useful as a state-level join key. |
| SITE_STATUS | Site Status | String(25) | Lifecycle status — filter for "active" sites |
| JURSCODE | County Name | String(25) | Coded values (ALLE, ANNE, BACI, …, 24 total — full Maryland jurisdiction list including Baltimore City) |
| SCHOOL_NAME | School Name | String(110) | |
| SCHOOL_DISTRICT_NAME | School District Name | String(50) | LEA |
| SCHOOL_STREET_ADDRESS | Address | String(255) | Useful as a fallback join key for Judy Center matching |
| SCHOOL_TYPE | School Level | String(50) | NCES standard: Elementary, Middle, High, Combined, Vocational/Technical, Special Education, Charter, Other, Unknown |
| SCHOOL_TYPE_DETAIL | Detailed School Level | String(10) | Breaks down "Combined" — this is what tells K-8 from K-12. Value domain not yet enumerated. |
| SCHOOL_YEAR | Current School Year | String(25) | Should be 2024-2025 currently |
| CHILD_CPG_PCT | Child Poverty Grant Percent | Double | Three-year FARMS rolling average |
| CHILD_CPG_STATUS | **ENOUGH Grant Eligibility Status** | String(30) | Alias updated for 2025 service (note: spelled "Eligibilty" in the field metadata) |
| NCESSCH_ID | NCES Unique School ID | String(30) | **Best join key for Judy Center matching** — federal NCES identifier |
| IAC_ID_1 | IAC Unique ID Number 1 | String(30) | Interagency Committee on School Construction ID |
| IAC_ID_2 | IAC Unique ID Number 2 | String(8) | |
| GOC_ID | **Governor's Office of Children Unique ID** | String(8) | The GOC's own per-school ID — present on the layer, ready for joining to internal GOC records |
| NOTES | Notes | String(255) | |
| CALC_SUM | CALC_SUM | Double | Internal calculation field |

## Layer 1 — School Attendance Zones 2025 (catchments)

Geometry: `esriGeometryPolygon`. Field schema not separately fetched, but the 2024 version of the catchment layer (`School_Cachement_Zones_Clipped` in the legacy service — note "Cachement" is misspelled in the source feature class) carried the same school-identification fields as Layer 0. Expected to mirror Layer 0's school fields plus `Shape_Length` and `Shape_Area`.

This layer is the basis for the user's planned follow-up analysis ("Judy Centers close to but outside grantee tract boundaries").

## Layer 2 — Poverty Census Tracts 2025 (tract polygons)

Geometry: `esriGeometryPolygon`.

| Field | Alias | Type | Notes |
|---|---|---|---|
| OBJECTID | OBJECTID | OID | Internal |
| JURSCODE | County Name | String(25) | Same coded values as Layer 0 |
| GEOID20 | geoid20 | String(25) | **Standard 11-digit Census tract ID** — primary key for joining to ACS, MDP CensusBoundaries, etc. |
| POPULATION_TOTAL | Total Population | Integer | |
| CHILD_POPULATION_TOTAL | Total Population Under 18 Years Old | Integer | Denominator for child poverty rate |
| CHILD_POV_TOTAL | Total Population Under 18 Years in Poverty | Integer | Numerator |
| CHILD_POV_PCT | Percent of Children in Poverty | Double | Source is ACS 5-year. **Vintage not specified in field metadata — needs confirmation.** |
| SCHOOL_BND_INT_TOTAL | Total Number of Intersecting School Attendance Boundaries | SmallInt | |
| SCHOOL_BND_INT_CPG_TOTAL | Total Number of Intersecting School Attendance Boundaries with >= 80% CPG | SmallInt | **Alias still says 80%, not 75%** — see Data quality flags below |
| NOTES | NOTES | String(2000) | |
| CALC_SUM, CALC_PCT | | Integer/Double | Internal calculation fields |

## Data quality flags / things to confirm with MDP

1. **CPG threshold (80% vs. 75%).** The aliases on `SCHOOL_BND_INT_CPG_TOTAL` (tract layer) and the 2024 catchment layer's `CHILD_CPG_STATUS` description still reference ≥80% CPG. The user reports the current map's About section uses 75%, and the schools layer has been split into "eligible" (≥75%) and "non-eligible" (<75%) front-end layers. Either the underlying values have been updated to reflect 75% but the field documentation hasn't, or the 75% threshold lives only in the front-end layer styling. Confirm with MDP.

2. **No ACS Margin of Error field on the tract layer.** Layer 2 has `CHILD_POV_PCT` but no corresponding `CHILD_POV_PCT_MOE` field. The new formula the GOC plans to roll out (allowing eligibility when point estimate + MOE > 30% AND prior year ≥ 30%) **cannot be implemented from this layer alone**. MDP will need to add the MOE field, or the calculation will need to pull MOE from `api.census.gov/data/{year}/acs/acs5` (variable `B17020_002M` for the count MOE, with denominator MOE for the rate calculation via the Census-published rate-MOE formula).

3. **No prior-year child poverty field on the tract layer.** Same as above — the new formula needs the prior-year point estimate as a gate. Either MDP adds a `CHILD_POV_PCT_PRIOR` field, or the analysis code joins last year's ACS vintage in alongside the current one.

4. **ACS vintage not declared.** `CHILD_POV_PCT` field metadata doesn't indicate which ACS 5-year vintage was used. As of April 2026, the most recently released vintage is 2019-2023 (released Dec 2024); 2020-2024 is expected late 2025 / early 2026. Worth confirming which is currently loaded.

5. **No grantee/served-tracts layer in the 2025 service.** Whatever join we do between grantees and tracts has to come from the GOC's internal roster, not from the public REST endpoint. (Note: the legacy `ENOUGH_Act/FeatureServer/0` does exist — possibly worth checking whether it carries grantee identity.)

6. **`SCHOOL_TYPE_DETAIL` value domain not enumerated.** First query against the layer should `groupBy` this field to see the actual values used (which K-8, K-12, K-5, PK-8, etc. labels appear). Affects the K-8 inclusion rule the user specified.

## Standard query patterns for execution

Once the analysis kicks off, these are the canonical query URLs:

```
# All Maryland school points
https://mdpgis.mdp.state.md.us/arcgis/rest/services/Society/ENOUGH_Act_2025/FeatureServer/0/query
  ?where=1=1&outFields=*&f=geojson&returnGeometry=true

# All elementary + combined schools only
https://mdpgis.mdp.state.md.us/arcgis/rest/services/Society/ENOUGH_Act_2025/FeatureServer/0/query
  ?where=SCHOOL_TYPE IN ('Elementary','Combined')&outFields=*&f=geojson

# All Maryland tract polygons
https://mdpgis.mdp.state.md.us/arcgis/rest/services/Society/ENOUGH_Act_2025/FeatureServer/2/query
  ?where=1=1&outFields=*&f=geojson&returnGeometry=true

# Pagination: append &resultOffset=N&resultRecordCount=2000 (MaxRecordCount is 2000)
```

Both layers support `f=geojson` natively, so output goes straight into geopandas with `read_file()` against the URL.

## Layers / sources NOT in this service that the analysis will need

- **Maryland 2020 census tract polygons** (canonical TIGER geometry, in case the ENOUGH service's polygons are clipped/simplified): `https://geodata.md.gov/imap/rest/services/Demographics/MD_CensusBoundaries/MapServer` — Census Tracts 2020 layer.
- **ACS 5-year child poverty estimates with MOE**: Census API, table `B17020` or `S1701`.
- **Judy Center locations**: Authoritative source is MSDE's coordinator contact PDF (`earlychildhood.marylandpublicschools.org/.../judycentercoordinatorlistwebsitefeb2015_.pdf`, last updated 12/9/2025). MSDE's web server returns 403 to automated fetches — this file needs to be downloaded manually by a human and supplied to the analysis. Counts in MSDE's own copy vary across pages (51 in one location, 69 in another, with FY2025-2030 expansion language committing to many more), so the PDF is the only source that's likely to give a definitive current list with host school names and addresses.
- **GOC grantee → tract roster**: Internal to GOC.
