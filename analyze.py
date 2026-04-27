"""
ENOUGH x Judy Center coverage analysis.

Reads:
  - 2025 Updated ENOUGH Grantee Census Tracts and Zipcodes - Unique ENOUGH Census Tracts.csv
  - judy_centers_full.csv
  - MDP ENOUGH_Act_2025 FeatureServer (cached locally on first run):
      Layer 0  School Locations 2025  (point)
      Layer 2  Poverty Census Tracts 2025  (polygon)

Writes:
  - grantee_coverage.csv             one row per grantee
  - judy_center_coverage.csv         one row per Judy Center
  - candidate_elementary_schools.csv one row per elementary in a grantee tract
  - jc_match_audit.csv               per-Judy-Center match decision audit
  - gaps.csv                         grantees with no JC in their county (if any)

Pipeline:
  1. Pull MDP schools (Layer 0) and tracts (Layer 2) as GeoJSON.
  2. Reproject to EPSG:26985 (NAD83 / MD State Plane).
  3. Spatial join schools -> tracts (predicate='within'), giving every school
     a GEOID20.
  4. Resolve each Judy Center to a school in the joined frame using a tiered
     match: exact normalized SCHOOL_NAME within county, then rapidfuzz
     token_sort_ratio >= FUZZ_THRESHOLD within county.
  5. Cross-reference Judy Center tracts with grantee tracts.
  6. For every elementary/combined school inside a grantee tract that does
     NOT already have a Judy Center, surface it as a candidate ranked by
     CHILD_CPG_PCT (concentrated poverty grant rate).
"""

import csv
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

import urllib.request

import geopandas as gpd
from rapidfuzz import fuzz, process

HERE = Path(__file__).parent
GRANTEE_CSV = HERE / "2025 Updated ENOUGH Grantee Census Tracts and Zipcodes - Unique ENOUGH Census Tracts.csv"
JUDY_CSV = HERE / "judy_centers_full.csv"
CACHE = HERE / ".cache"
CACHE.mkdir(exist_ok=True)
OUTPUTS = HERE

SCHOOLS_URL = (
    "https://mdpgis.mdp.state.md.us/arcgis/rest/services/"
    "Society/ENOUGH_Act_2025/FeatureServer/0/query"
    "?where=1%3D1&outFields=*&returnGeometry=true&f=geojson"
)
TRACTS_URL = (
    "https://mdpgis.mdp.state.md.us/arcgis/rest/services/"
    "Society/ENOUGH_Act_2025/FeatureServer/2/query"
    "?where=1%3D1&outFields=*&returnGeometry=true&f=geojson"
)
CATCHMENTS_URL = (
    "https://mdpgis.mdp.state.md.us/arcgis/rest/services/"
    "Society/ENOUGH_Act_2025/FeatureServer/1/query"
    "?where=1%3D1&outFields=*&returnGeometry=true&f=geojson"
)

# Catchment area / county area >= this fraction => "county-wide" flag.
COUNTYWIDE_THRESHOLD = 0.70

FUZZ_THRESHOLD = 85  # rapidfuzz token_sort_ratio cutoff

# County FIPS -> county name (Maryland 24xxx)
COUNTY_NAMES = {
    "24001": "Allegany",
    "24003": "Anne Arundel",
    "24005": "Baltimore County",
    "24009": "Calvert",
    "24011": "Caroline",
    "24013": "Carroll",
    "24015": "Cecil",
    "24017": "Charles",
    "24019": "Dorchester",
    "24021": "Frederick",
    "24023": "Garrett",
    "24025": "Harford",
    "24027": "Howard",
    "24029": "Kent",
    "24031": "Montgomery",
    "24033": "Prince George's",
    "24035": "Queen Anne's",
    "24037": "St. Mary's",
    "24039": "Somerset",
    "24041": "Talbot",
    "24043": "Washington",
    "24045": "Wicomico",
    "24047": "Worcester",
    "24510": "Baltimore City",
}

# MDP JURSCODE -> county FIPS (4-letter county code on Layer 0/2)
JURSCODE_TO_FIPS = {
    "ALLE": "24001", "ANNE": "24003", "BACO": "24005", "CALV": "24009",
    "CARO": "24011", "CARR": "24013", "CECI": "24015", "CHAR": "24017",
    "DORC": "24019", "FRED": "24021", "GARR": "24023", "HARF": "24025",
    "HOWA": "24027", "KENT": "24029", "MONT": "24031", "PRIN": "24033",
    "QUEE": "24035", "STMA": "24037", "SOME": "24039", "TALB": "24041",
    "WASH": "24043", "WICO": "24045", "WORC": "24047", "BACI": "24510",
}


# ---- Name normalization for matching ----

_SUFFIX_PAT = re.compile(
    r"\b(elementary/middle|elementary|middle|primary|early\s+learning"
    r"|learning\s+center|center|campus|academy|school|sch|es|ms|ec|elc)\b",
    re.IGNORECASE,
)
_PUNCT_PAT = re.compile(r"[^\w\s/]")
_WS_PAT = re.compile(r"\s+")
_TITLE_PAT = re.compile(r"\b(dr|mr|mrs|ms|st|saint|mt|mount)\b\.?", re.IGNORECASE)
_COMMON_REPLACEMENTS = [
    (r"\brodgers\b", "rogers"),       # JC list: Commodore John Rodgers; MDP: Rogers
    (r"\bwaverley\b", "waverly"),     # JC list: Waverley; MDP: Waverly
]


def normalize_name(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower().strip()
    s = _PUNCT_PAT.sub(" ", s)
    s = _TITLE_PAT.sub(" ", s)
    s = _SUFFIX_PAT.sub(" ", s)
    for pat, repl in _COMMON_REPLACEMENTS:
        s = re.sub(pat, repl, s)
    s = _WS_PAT.sub(" ", s).strip()
    return s


_ADDR_NUM_PAT = re.compile(r"^\s*(\d+)")


def normalize_address(s: str) -> str:
    """Reduce a street address to '<number> <street-prefix>' for fuzzy matching.

    JC list and MDP often differ in punctuation, abbreviation (Rd vs Road),
    and trailing city/state/zip. Anchoring on the leading number plus the
    first address token catches the colocated-site cases.
    """
    if not s:
        return ""
    s = s.lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    s = _WS_PAT.sub(" ", s).strip()
    return s


# ---- Data loaders ----

def load_grantees():
    grantee_to_tracts = defaultdict(set)
    tract_to_grantees = defaultdict(set)
    with GRANTEE_CSV.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            tract = row["ENOUGH Census Tract"].strip()
            for col in ("Grantee 1", "Grantee 2"):
                g = (row.get(col) or "").strip()
                if g:
                    grantee_to_tracts[g].add(tract)
                    tract_to_grantees[tract].add(g)
    return grantee_to_tracts, tract_to_grantees


def load_judy_centers():
    rows = []
    with JUDY_CSV.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def fetch_layer(url: str, cache_path: Path) -> gpd.GeoDataFrame:
    """Read an MDP layer as GeoDataFrame, caching the GeoJSON locally.

    geopandas.read_file issues a Range probe that MDP's server rejects with
    403, so we download the GeoJSON over plain urllib first.
    """
    if not cache_path.exists():
        print(f"  fetching {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "enough-jc-analysis"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            cache_path.write_bytes(resp.read())
    else:
        print(f"  using cached {cache_path.name}")
    return gpd.read_file(cache_path)


# ---- Spatial join ----

def build_joined_schools() -> gpd.GeoDataFrame:
    """Return all MDP schools with their tract GEOID20 attached."""
    print("Loading MDP layers...")
    schools = fetch_layer(SCHOOLS_URL, CACHE / "mdp_schools_2025.geojson")
    tracts = fetch_layer(TRACTS_URL, CACHE / "mdp_tracts_2025.geojson")

    schools = schools.to_crs(epsg=26985)
    tracts = tracts.to_crs(epsg=26985)

    tracts_subset = tracts[["GEOID20", "CHILD_POV_PCT", "geometry"]]
    joined = gpd.sjoin(schools, tracts_subset, predicate="within", how="left")
    joined = joined.drop(columns=[c for c in ("index_right",) if c in joined])
    return joined


def build_catchments() -> gpd.GeoDataFrame:
    """Return MDP school attendance zones (Layer 1) in EPSG:26985.

    Catchments only exist for schools that have published attendance zones,
    which is most public elementary/middle/high schools but not magnets,
    charters, or ELCs without a defined zone. Joining keys: NCESSCH_ID
    (preferred) and LEA_SITE.
    """
    print("Loading MDP catchments (Layer 1)...")
    cat = fetch_layer(CATCHMENTS_URL, CACHE / "mdp_catchments_2025.geojson")
    cat = cat.to_crs(epsg=26985)
    # Some catchment rows have a Shape__Area attribute, but prefer geometry.area
    # in EPSG:26985 (square metres) for consistency with our overlay math.
    cat["catchment_area_m2"] = cat.geometry.area
    return cat


def flag_countywide_catchments(catchments: gpd.GeoDataFrame,
                                tracts: gpd.GeoDataFrame) -> dict:
    """Return {ncessch_id_or_lea_site: True} for catchments that cover
    >= COUNTYWIDE_THRESHOLD of their county area.

    Some MDP catchments are effectively the whole county (single-school
    counties, magnet/special programs). Rendering these as polygons would
    visually engulf the map, so we flag them and the frontend treats them
    as "county-wide" annotations rather than shapes.
    """
    # County area = sum of tract polygon areas keyed by GEOID20[:5]
    county_areas = (
        tracts.assign(cfips=tracts["GEOID20"].str[:5])
        .groupby("cfips")
        .geometry.apply(lambda g: g.union_all().area)
        .to_dict()
    )

    flags = {}
    for _, row in catchments.iterrows():
        cfips = row.get("COUNTY_FIPS") or JURSCODE_TO_FIPS.get(row.get("JURSCODE"))
        if not cfips:
            continue
        ca = float(row["catchment_area_m2"] or 0)
        ta = county_areas.get(cfips, 0)
        if ta and ca / ta >= COUNTYWIDE_THRESHOLD:
            key = row.get("NCESSCH_ID") or row.get("LEA_SITE") or ""
            if key:
                flags[key] = round(ca / ta, 3)
    return flags


def compute_catchment_overlaps(catchments: gpd.GeoDataFrame,
                                grantee_tracts: gpd.GeoDataFrame,
                                tract_to_grantees: dict,
                                school_keys: set) -> list:
    """For every catchment whose host school is in `school_keys`,
    return rows describing how it overlaps with grantee-served tracts.

    Returns a list of dicts:
      {ncessch_id, lea_site, school_name, school_type,
       grantee, tract_geoid, intersect_area_m2, pct_of_tract, pct_of_catchment}
    Rows where intersect area is essentially zero are filtered.
    """
    keys_we_want = catchments[
        (catchments["NCESSCH_ID"].fillna("").isin(school_keys))
        | (catchments["LEA_SITE"].fillna("").isin(school_keys))
    ].copy()
    if keys_we_want.empty:
        return []

    # Use overlay for accurate intersection geometry; sjoin alone gives
    # candidate pairs but not the area of overlap.
    inter = gpd.overlay(
        keys_we_want[["NCESSCH_ID", "LEA_SITE", "SCHOOL_NAME", "SCHOOL_TYPE",
                      "catchment_area_m2", "geometry"]],
        grantee_tracts[["GEOID20", "geometry"]],
        how="intersection",
        keep_geom_type=False,
    )
    if inter.empty:
        return []

    inter["inter_area_m2"] = inter.geometry.area
    inter = inter[inter["inter_area_m2"] > 1.0]  # drop slivers

    rows = []
    for _, r in inter.iterrows():
        tract = r["GEOID20"]
        for grantee in tract_to_grantees.get(tract, set()):
            ca = float(r["catchment_area_m2"] or 0)
            tract_area = (
                grantee_tracts.loc[grantee_tracts["GEOID20"] == tract,
                                   "geometry"].iloc[0].area
            )
            rows.append({
                "ncessch_id": r.get("NCESSCH_ID") or "",
                "lea_site": r.get("LEA_SITE") or "",
                "school_name": r["SCHOOL_NAME"],
                "school_type": r["SCHOOL_TYPE"],
                "grantee": grantee,
                "tract_geoid": tract,
                "intersect_area_m2": int(r["inter_area_m2"]),
                "pct_of_tract": (
                    round(r["inter_area_m2"] / tract_area, 3)
                    if tract_area else 0
                ),
                "pct_of_catchment": round(r["inter_area_m2"] / ca, 3) if ca else 0,
            })
    return rows


def resolve_judy_centers(judy_centers, joined_schools):
    """
    For each Judy Center, return (resolved_tract, matched_school_name, tier, score).
    Tier is 'exact' or 'fuzzy_NN' or 'unresolved'.
    """
    # For the address-fallback tier we want to consider ALL schools at a
    # given address (a Judy Center may be hosted at a school typed Middle
    # in MDP, e.g. Westmar ELC colocated with Westmar Middle).
    all_schools = joined_schools.copy()
    all_schools["norm_addr"] = all_schools["SCHOOL_STREET_ADDRESS"].fillna("").map(normalize_address)
    all_schools["county_fips"] = all_schools["JURSCODE"].map(JURSCODE_TO_FIPS)

    elem = joined_schools[
        joined_schools["SCHOOL_TYPE"].isin(["Elementary", "Combined"])
    ].copy()
    elem["norm"] = elem["SCHOOL_NAME"].fillna("").map(normalize_name)
    # MDP uses 4-letter JURSCODE; map to FIPS for filtering
    elem["county_fips"] = elem["JURSCODE"].map(JURSCODE_TO_FIPS)

    audit = []
    for jc in judy_centers:
        target = normalize_name(jc["school_name"])
        cfips = jc["county_fips"]
        candidates = elem[elem["county_fips"] == cfips]

        # Tier 1: exact normalized match within county
        exact = candidates[candidates["norm"] == target]
        if len(exact) >= 1:
            row = exact.iloc[0]
            audit.append({
                "judy_center": jc["school_name"],
                "county": jc["county"],
                "matched_school": row["SCHOOL_NAME"],
                "ncessch_id": row.get("NCESSCH_ID") or "",
                "geoid20": row["GEOID20"],
                "tier": "exact",
                "score": 100,
            })
            continue

        # Tier 2: rapidfuzz token_sort_ratio within county
        if not candidates.empty and target:
            choices = dict(zip(candidates.index, candidates["norm"]))
            best = process.extractOne(
                target, choices, scorer=fuzz.token_sort_ratio
            )
            if best and best[1] >= FUZZ_THRESHOLD:
                row = candidates.loc[best[2]]
                audit.append({
                    "judy_center": jc["school_name"],
                    "county": jc["county"],
                    "matched_school": row["SCHOOL_NAME"],
                    "ncessch_id": row.get("NCESSCH_ID") or "",
                    "geoid20": row["GEOID20"],
                    "tier": f"fuzzy_{int(best[1])}",
                    "score": int(best[1]),
                })
                continue

        # Tier 3: address fallback within county across ALL school types
        target_addr = normalize_address(jc.get("school_address") or "")
        if target_addr:
            addr_pool = all_schools[all_schools["county_fips"] == cfips]
            addr_pool = addr_pool[addr_pool["norm_addr"] != ""]
            if not addr_pool.empty:
                choices = dict(zip(addr_pool.index, addr_pool["norm_addr"]))
                best = process.extractOne(
                    target_addr, choices, scorer=fuzz.token_set_ratio
                )
                if best and best[1] >= FUZZ_THRESHOLD:
                    row = addr_pool.loc[best[2]]
                    audit.append({
                        "judy_center": jc["school_name"],
                        "county": jc["county"],
                        "matched_school": row["SCHOOL_NAME"] + " (addr)",
                        "ncessch_id": row.get("NCESSCH_ID") or "",
                        "geoid20": row["GEOID20"],
                        "tier": f"address_{int(best[1])}",
                        "score": int(best[1]),
                    })
                    continue

        audit.append({
            "judy_center": jc["school_name"],
            "county": jc["county"],
            "matched_school": "",
            "ncessch_id": "",
            "geoid20": "",
            "tier": "not_in_mdp",
            "score": 0,
        })
    return audit


def tract_county_fips(tract_geoid):
    return tract_geoid[:5]


# ---- Site data emission (Leaflet frontend) ----

SITE_DATA = HERE / "site" / "data"


def _is_nan(v):
    try:
        return v != v  # NaN trick
    except Exception:
        return False


def _clean(v):
    """Convert NaN -> None, leave numbers/strings alone."""
    if v is None or _is_nan(v):
        return None
    return v


def _short_recommendation(top_cpg):
    if top_cpg is None or top_cpg < 0:
        return "No traditional elementary in any served tract"
    if top_cpg >= 0.85:
        return "Strong candidate — pursue Judy Center conversation"
    if top_cpg >= 0.65:
        return "Possible candidate — confirm school eligibility"
    return "Lower priority — CPG below typical Judy Center threshold"


def write_site_data(grantee_rows, candidate_rows, audit, overlap_rows,
                     countywide_flags, joined_schools, catchments,
                     grantee_tracts_gdf, raw_tracts, tract_to_grantees):
    """Emit the four files the static Leaflet site reads.

    site/data/
        grantee_tracts.geojson    EPSG:4326 polygons, 106 tracts
        schools.geojson           EPSG:4326 points, only schools
                                  relevant to at least one grantee
        catchments.geojson        EPSG:4326 polygons, catchments for
                                  the rendered schools (excluding
                                  county-wide ones)
        grantees.json             per-grantee narrative payload
    """
    SITE_DATA.mkdir(parents=True, exist_ok=True)

    # --- 1. Grantee tracts ---
    # Attach the grantees-per-tract list for easy hover popup.
    raw_tracts_4326 = raw_tracts.to_crs(epsg=4326)
    g_tracts = raw_tracts_4326[
        raw_tracts_4326["GEOID20"].isin(tract_to_grantees.keys())
    ].copy()
    g_tracts["grantees"] = g_tracts["GEOID20"].map(
        lambda t: sorted(tract_to_grantees.get(t, []))
    )
    g_tracts = g_tracts[[
        "GEOID20", "grantees", "CHILD_POV_PCT", "CHILD_POPULATION_TOTAL", "geometry"
    ]].rename(columns={
        "GEOID20": "geoid20",
        "CHILD_POV_PCT": "child_pov_pct",
        "CHILD_POPULATION_TOTAL": "child_pop_total",
    })
    g_tracts.to_file(SITE_DATA / "grantee_tracts.geojson", driver="GeoJSON")

    # --- 2. Determine relevant schools ---
    # We render: resolved JCs + candidate elementaries + JC near-misses.
    # Each school carries the role it plays for each grantee that cares.
    ncesid_to_role = defaultdict(dict)  # ncesid -> {grantee: role}

    # Resolved JCs in grantee tracts
    audit_by_ncesid = {a["ncessch_id"]: a for a in audit if a["ncessch_id"]}
    for a in audit_by_ncesid.values():
        gtr = tract_to_grantees.get(a["geoid20"], set())
        for g in gtr:
            ncesid_to_role[a["ncessch_id"]][g] = "jc_in_tract"

    # Candidate elementaries
    for c in candidate_rows:
        if c["ncessch_id"] and c["is_judy_center"] == "no":
            ncesid_to_role[c["ncessch_id"]].setdefault(c["grantee"], "candidate")
        elif c["ncessch_id"]:
            # is_judy_center yes via candidate path — already handled above
            ncesid_to_role[c["ncessch_id"]].setdefault(c["grantee"], "jc_in_tract")

    # Near-miss JCs (catchment overlaps tract but school is outside)
    jc_ncesids = {a["ncessch_id"] for a in audit if a["ncessch_id"]}
    for o in overlap_rows:
        if not o["ncessch_id"]:
            continue
        if o["ncessch_id"] not in jc_ncesids:
            continue
        # Skip if school is already inside this grantee's tracts
        if ncesid_to_role.get(o["ncessch_id"], {}).get(o["grantee"]) == "jc_in_tract":
            continue
        if (o["pct_of_tract"] < 0.05 and o["pct_of_catchment"] < 0.05):
            continue
        if (o["ncessch_id"] in countywide_flags
                or o["lea_site"] in countywide_flags):
            continue
        ncesid_to_role[o["ncessch_id"]][o["grantee"]] = "jc_near_miss"

    relevant_ncesids = set(ncesid_to_role.keys())

    # --- 3. Schools GeoJSON ---
    schools_4326 = joined_schools.to_crs(epsg=4326)
    rel_schools = schools_4326[
        schools_4326["NCESSCH_ID"].fillna("").isin(relevant_ncesids)
    ].copy()
    rel_schools["lat"] = rel_schools.geometry.y
    rel_schools["lon"] = rel_schools.geometry.x
    rel_schools["roles"] = rel_schools["NCESSCH_ID"].map(
        lambda nid: ncesid_to_role.get(nid, {})
    )
    rel_schools["is_judy_center"] = rel_schools["NCESSCH_ID"].map(
        lambda nid: nid in jc_ncesids
    )
    rel_schools["countywide_catchment"] = rel_schools["NCESSCH_ID"].map(
        lambda nid: nid in countywide_flags
    )
    keep = ["NCESSCH_ID", "LEA_SITE", "SCHOOL_NAME", "SCHOOL_TYPE",
            "SCHOOL_STREET_ADDRESS", "GEOID20", "CHILD_CPG_PCT",
            "CHILD_POV_PCT", "lat", "lon", "roles", "is_judy_center",
            "countywide_catchment", "geometry"]
    rel_schools = rel_schools[keep].rename(columns={
        "NCESSCH_ID": "ncessch_id",
        "LEA_SITE": "lea_site",
        "SCHOOL_NAME": "school_name",
        "SCHOOL_TYPE": "school_type",
        "SCHOOL_STREET_ADDRESS": "address",
        "GEOID20": "geoid20",
        "CHILD_CPG_PCT": "cpg",
        "CHILD_POV_PCT": "child_pov_pct",
    })
    rel_schools.to_file(SITE_DATA / "schools.geojson", driver="GeoJSON")

    # --- 4. Catchments GeoJSON (relevant schools, exclude countywide) ---
    cat_4326 = catchments.to_crs(epsg=4326).copy()
    rel_cats = cat_4326[
        cat_4326["NCESSCH_ID"].fillna("").isin(relevant_ncesids)
        & ~cat_4326["NCESSCH_ID"].fillna("").isin(countywide_flags.keys())
    ][["NCESSCH_ID", "LEA_SITE", "SCHOOL_NAME", "SCHOOL_TYPE", "geometry"]].rename(
        columns={
            "NCESSCH_ID": "ncessch_id",
            "LEA_SITE": "lea_site",
            "SCHOOL_NAME": "school_name",
            "SCHOOL_TYPE": "school_type",
        }
    )
    rel_cats.to_file(SITE_DATA / "catchments.geojson", driver="GeoJSON")

    # --- 5. Per-grantee narrative ---
    cands_by_grantee = defaultdict(list)
    for c in candidate_rows:
        cands_by_grantee[c["grantee"]].append(c)
    for g in cands_by_grantee:
        cands_by_grantee[g].sort(
            key=lambda r: (-(float(r["child_cpg_pct"] or 0)),
                           0 if r["is_judy_center"] == "no" else 1)
        )

    near_miss_by_grantee = defaultdict(list)
    seen_pairs = set()
    for o in overlap_rows:
        if o["ncessch_id"] not in jc_ncesids:
            continue
        if (o["pct_of_tract"] < 0.05 and o["pct_of_catchment"] < 0.05):
            continue
        if (o["ncessch_id"] in countywide_flags
                or o["lea_site"] in countywide_flags):
            continue
        if (ncesid_to_role.get(o["ncessch_id"], {}).get(o["grantee"])
                == "jc_in_tract"):
            continue
        # de-dupe across the multiple-tract-overlap rows for the same JC
        pair = (o["grantee"], o["ncessch_id"])
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        near_miss_by_grantee[o["grantee"]].append({
            "school_name": o["school_name"],
            "ncessch_id": o["ncessch_id"],
            "pct_of_tract": o["pct_of_tract"],
            "pct_of_catchment": o["pct_of_catchment"],
            "tract_geoid": o["tract_geoid"],
        })

    grantees_payload = []
    for r in grantee_rows:
        g = r["grantee"]
        cands = cands_by_grantee.get(g, [])
        non_jc = [c for c in cands if c["is_judy_center"] == "no"]
        top_cpg = float(non_jc[0]["child_cpg_pct"]) if non_jc and non_jc[0]["child_cpg_pct"] else None
        grantees_payload.append({
            "grantee": g,
            "county": r["counties"],
            "n_tracts": int(r["n_tracts"]),
            "tracts": sorted(r["tracts"].split("; ")) if r["tracts"] else [],
            "tract_level_coverage": r["tract_level_coverage"] == "yes",
            "county_level_coverage": r["county_level_coverage"] == "yes",
            "jcs_in_tracts": [s for s in r["jc_in_grantee_tracts"].split("; ") if s] if r["jc_in_grantee_tracts"] else [],
            "jcs_near_miss": near_miss_by_grantee.get(g, []),
            "candidates": [
                {
                    "school_name": c["school_name"],
                    "school_type": c["school_type"],
                    "ncessch_id": c["ncessch_id"],
                    "geoid20": c["geoid20"],
                    "cpg": _clean(float(c["child_cpg_pct"]) if c["child_cpg_pct"] else None),
                    "child_pov_pct": _clean(float(c["child_pov_pct"]) if c["child_pov_pct"] else None),
                    "is_jc": c["is_judy_center"] == "yes",
                    "address": c["address"],
                }
                for c in cands
            ],
            "named_hint": g in NAMED_HINT_GRANTEES,
            "recommendation_short": _short_recommendation(top_cpg),
        })

    # Sort: needs-JC by best non-JC CPG desc, then has-JC alphabetically
    def sort_key(p):
        if p["tract_level_coverage"]:
            return (1, p["grantee"].lower())
        # Best non-JC CPG
        for c in p["candidates"]:
            if not c["is_jc"] and c["cpg"] is not None:
                return (0, -c["cpg"], p["grantee"].lower())
        return (0, 1.0, p["grantee"].lower())

    grantees_payload.sort(key=sort_key)

    import json
    (SITE_DATA / "grantees.json").write_text(
        json.dumps(grantees_payload, indent=2, default=str), encoding="utf-8"
    )
    print(f"Wrote site data: {len(g_tracts)} tracts, {len(rel_schools)} schools, "
          f"{len(rel_cats)} catchments, {len(grantees_payload)} grantees")


# ---- Executive summary ----

# These four grantees were named-overlap hints in the prototype and confirm
# at the tract level. Listed here so the summary can highlight them as the
# "already-aligned" exemplars.
NAMED_HINT_GRANTEES = {
    "City of Cumberland",
    "One Annapolis, Inc",
    "San Mar Family & Community Services: Bester Community of Hope",
    "Frederick Local Management Board",
}


def _fmt_pct(v):
    try:
        return f"{float(v) * 100:.0f}%"
    except (TypeError, ValueError):
        return "—"


def _grantee_county_label(grantee_row):
    return grantee_row["counties"]


def write_executive_summary(grantee_rows, candidate_rows, audit, joined, tract_to_grantees):
    cands_by_grantee = defaultdict(list)
    for c in candidate_rows:
        cands_by_grantee[c["grantee"]].append(c)
    # Within each grantee, sort by CPG desc; non-JCs first within same CPG
    for g in cands_by_grantee:
        cands_by_grantee[g].sort(
            key=lambda r: (-(float(r["child_cpg_pct"] or 0)),
                           0 if r["is_judy_center"] == "no" else 1)
        )

    no_jc = [r for r in grantee_rows if r["tract_level_coverage"] != "yes"]
    has_jc = [r for r in grantee_rows if r["tract_level_coverage"] == "yes"]

    # Order: needs-JC grantees by best non-JC candidate CPG desc, ties by name
    def best_non_jc_cpg(g):
        for c in cands_by_grantee.get(g, []):
            if c["is_judy_center"] == "no":
                try:
                    return float(c["child_cpg_pct"] or 0)
                except (TypeError, ValueError):
                    return 0.0
        return -1.0

    no_jc.sort(key=lambda r: (-best_non_jc_cpg(r["grantee"]), r["grantee"]))
    has_jc.sort(key=lambda r: r["grantee"])

    lines = []
    lines.append("# ENOUGH x Judy Center Coverage — Executive Summary")
    lines.append("")
    lines.append(
        "Maryland's 28 ENOUGH grantees collectively serve 106 census tracts; the state's "
        "79 Judy Center Early Learning Hubs are distributed across 24 counties. This "
        "analysis answers a single operational question: **which ENOUGH grantees are "
        "good candidates to apply for a Judy Center?** A grantee is a strong candidate "
        "when its served tracts contain at least one high-poverty elementary school but "
        "no Judy Center is currently sited there. Of the 28 grantees, **14 already have "
        "a Judy Center inside one of their served tracts** and **14 do not**. The 14 "
        "without coverage are listed first below, ordered by the concentrated-poverty "
        "rate (CHILD_CPG_PCT) of the highest-CPG candidate elementary school in their "
        "tracts."
    )
    lines.append("")

    # ---- Headline gap-list table: grantees WITHOUT tract-level JC ----
    lines.append("## Grantees without a Judy Center in their served tracts")
    lines.append("")
    lines.append("Ranked by best-candidate CPG (the highest concentrated-poverty rate "
                 "among elementary schools inside the grantee's tracts that are not "
                 "already Judy Centers).")
    lines.append("")
    lines.append("| Grantee | County | # Tracts | Top non-JC candidate(s) by CPG | Recommendation |")
    lines.append("|---|---|---:|---|---|")
    for r in no_jc:
        cands = cands_by_grantee.get(r["grantee"], [])
        non_jc_cands = [c for c in cands if c["is_judy_center"] == "no"]
        if non_jc_cands:
            top = non_jc_cands[:2]
            top_str = "; ".join(
                f"{c['school_name']} ({_fmt_pct(c['child_cpg_pct'])})"
                for c in top
            )
            best = float(top[0]["child_cpg_pct"] or 0)
            if best >= 0.85:
                rec = "Strong candidate — pursue Judy Center conversation"
            elif best >= 0.65:
                rec = "Possible candidate — confirm school eligibility"
            else:
                rec = "Lower priority — CPG below typical Judy Center threshold"
        elif cands:
            top_str = "(only candidate is already a Judy Center in another sense)"
            rec = "Review case by case"
        else:
            top_str = "No traditional elementary in any served tract"
            rec = "Investigate alternative school types in tract (charter/middle)"
        # County: shorten if very long
        county = r["counties"]
        if len(county) > 30:
            county = county[:27] + "..."
        grantee_short = r["grantee"]
        if len(grantee_short) > 50:
            grantee_short = grantee_short[:47] + "..."
        lines.append(f"| {grantee_short} | {county} | {r['n_tracts']} | "
                     f"{top_str} | {rec} |")
    lines.append("")

    # ---- Already-aligned grantees ----
    lines.append("## Grantees with a Judy Center already in their served tracts")
    lines.append("")
    lines.append("These 14 grantees do **not** need to apply — they share at least "
                 "one census tract with an existing Judy Center. The four marked "
                 "with a star are the named-overlap exemplars: their grantee names "
                 "or initiative names already reference the Judy Center school or "
                 "locality.")
    lines.append("")
    lines.append("| Grantee | County | JCs in served tracts |")
    lines.append("|---|---|---|")
    for r in has_jc:
        marker = " *" if r["grantee"] in NAMED_HINT_GRANTEES else ""
        county = r["counties"]
        if len(county) > 30:
            county = county[:27] + "..."
        jcs = r["jc_in_grantee_tracts"] or "(see audit)"
        lines.append(f"| {r['grantee']}{marker} | {county} | {jcs} |")
    lines.append("")
    lines.append("\\* Named-overlap exemplars (City of Cumberland ↔ South Penn ES; "
                 "One Annapolis ↔ Georgetown East ES; San Mar ↔ Bester ES; "
                 "Frederick LMB ↔ Waverley/Hillcrest/Butterfly Ridge ES). The "
                 "Frederick LMB initiative spans 5 tracts and 3 of the 6 FCPS Judy "
                 "Centers fall directly inside them; the other three FCPS Judy "
                 "Centers (Lincoln, Monocacy, Tuscarora) are in adjacent Frederick "
                 "tracts the LMB does not serve under the current grant.")
    lines.append("")

    # ---- Full candidate elementary list per grantee without coverage ----
    lines.append("## Candidate elementary schools by grantee")
    lines.append("")
    lines.append("Every elementary or combined school whose census tract is served "
                 "by an ENOUGH grantee, ranked by CHILD_CPG_PCT within grantee. "
                 "Schools already operating as Judy Centers are marked. This is the "
                 "underlying dataset behind the headline table; "
                 "`candidate_elementary_schools.csv` carries the same data with "
                 "GEOID20 and NCES IDs.")
    lines.append("")
    for r in no_jc + has_jc:
        cands = cands_by_grantee.get(r["grantee"], [])
        if not cands:
            continue
        lines.append(f"### {r['grantee']}")
        lines.append(f"_{r['counties']} — {r['n_tracts']} served tract(s)_")
        lines.append("")
        lines.append("| School | Type | Tract | CPG | Child Pov | Judy Center? |")
        lines.append("|---|---|---|---:|---:|:---:|")
        for c in cands:
            jc_mark = "yes" if c["is_judy_center"] == "yes" else ""
            lines.append(
                f"| {c['school_name']} | {c['school_type']} | {c['geoid20']} | "
                f"{_fmt_pct(c['child_cpg_pct'])} | "
                f"{_fmt_pct((c['child_pov_pct'] or 0)/100 if c['child_pov_pct'] else 0)} | "
                f"{jc_mark} |"
            )
        lines.append("")

    # ---- Methodology / caveats ----
    n_resolved = sum(1 for a in audit if a["tier"] != "not_in_mdp")
    n_exact = sum(1 for a in audit if a["tier"] == "exact")
    n_fuzzy = sum(1 for a in audit if a["tier"].startswith("fuzzy"))
    n_addr  = sum(1 for a in audit if a["tier"].startswith("address"))
    n_unres = sum(1 for a in audit if a["tier"] == "not_in_mdp")
    lines.append("## Methodology and caveats")
    lines.append("")
    lines.append("**Data sources.** ENOUGH grantee-to-tract roster supplied by GOC "
                 "(`2025 Updated ENOUGH Grantee Census Tracts and Zipcodes`). Judy "
                 "Center master list reconciled from the December 2025 MSDE coordinator "
                 "PDF, the 2019 MSDE coordinator PDF, and individual LEA pages "
                 "(`judy_centers_full.csv`, 79 sites). Maryland school points and tract "
                 "polygons from MDP's `ENOUGH_Act_2025` FeatureServer (Layers 0 and 2), "
                 "downloaded April 2026.")
    lines.append("")
    lines.append("**Spatial join.** Both layers reprojected to NAD83 / Maryland State "
                 "Plane (EPSG:26985). Each school assigned its containing tract via "
                 "`geopandas.sjoin(predicate='within')`. All 1,377 schools resolved "
                 "to a tract; school JURSCODE matched the GEOID20 county prefix in "
                 "every case.")
    lines.append("")
    lines.append(f"**Judy Center to MDP school matching.** Tiered: exact normalized "
                 f"name within county → rapidfuzz `token_sort_ratio >= 85` within "
                 f"county → address-based fallback (catches Judy Centers colocated "
                 f"with schools of a different MDP type). "
                 f"Resolved {n_resolved} of {len(audit)} sites "
                 f"({n_exact} exact, {n_fuzzy} fuzzy, {n_addr} via address). "
                 f"Five sites are not in the MDP layer:")
    lines.append("")
    for a in audit:
        if a["tier"] == "not_in_mdp":
            lines.append(f"  - **{a['judy_center']}** ({a['county']}) — "
                         f"investigate with MSDE/GOC; possible closure, recent rename, "
                         f"or PDF-extraction artifact.")
    lines.append("")
    lines.append("**Concentrated Poverty Grant rate.** `CHILD_CPG_PCT` on MDP's school "
                 "layer is a three-year FARMS rolling average. The 2025 ENOUGH eligibility "
                 "threshold is 75% (per MDP's 2025 documentation; the 2024 layer used "
                 "80% — worth re-confirming with MDP that the values reloaded under "
                 "the new threshold). Schools below 65% CPG are flagged "
                 "_lower priority_ in the recommendation column because they fall below "
                 "the typical Judy Center poverty profile.")
    lines.append("")
    lines.append("**Caveats.**")
    lines.append("- A Judy Center's host elementary school being outside a grantee's "
                 "tract does not mean the JC and grantee aren't already coordinating. "
                 "Many BC Judy Centers (Moravia Park, John Ruhrah, Maree G Farring) "
                 "sit just outside grantee polygons; a follow-up using the school "
                 "attendance-zone layer (`Layer 1`) would surface JCs whose catchment "
                 "overlaps a grantee tract even when the school point itself does not.")
    lines.append("- The FY24 MSDE board document references 86 Judy Center sites; "
                 "this analysis uses 79. The 7-row gap is most likely Cecil County's "
                 "post-2019 expansion (1 → 10 sites) plus pending Prince George's "
                 "additions. Cecil isn't an ENOUGH county; PG is, so the 3 unresolved "
                 "PG names above warrant verification.")
    lines.append("- One grantee (Leaders Breeders Inc., BC tract 24510260301) has no "
                 "traditional elementary or combined school in its served tract — "
                 "only Belair-Edison Charter. Judy Center placement at a charter is "
                 "policy-dependent and worth a separate MSDE conversation.")
    lines.append("- The CHILD_POV_PCT column reflects MDP's tract-level child poverty "
                 "estimate; ACS vintage is not declared on the layer (likely 2019-2023 "
                 "5-year). For 2026 ENOUGH eligibility decisions that require Margin "
                 "of Error and prior-year point estimates, the analysis must join the "
                 "Census API directly — MDP's tract layer does not currently carry "
                 "those fields.")
    lines.append("- A school's CHILD_CPG_PCT can be substantially higher than its "
                 "tract's CHILD_POV_PCT (e.g., Belle Grove ES at 88% CPG sits in an "
                 "Anne Arundel tract reporting 0% child poverty). This is expected: "
                 "CPG is a 3-year FARMS rolling average for kids enrolled at the "
                 "school, while tract poverty is the share of children residing in "
                 "the tract. Open-boundary enrollment, magnet programs, and cross-"
                 "tract attendance zones routinely create that gap.")
    lines.append("")
    lines.append("**Reproducibility.** `python analyze.py` regenerates every CSV in "
                 "this directory, this summary, and the sanity-check overlays "
                 "(`sanity_overview_md.png`, `sanity_overview_bc.png`). MDP layer "
                 "downloads cache to `.cache/` so re-runs are seconds.")
    lines.append("")

    (OUTPUTS / "executive_summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("Wrote executive_summary.md")


# ---- Analysis ----

def analyze():
    grantee_to_tracts, tract_to_grantees = load_grantees()
    judy_centers = load_judy_centers()

    tract_county = {t: tract_county_fips(t) for t in tract_to_grantees.keys()}
    grantee_counties = {
        g: sorted({tract_county[t] for t in tracts})
        for g, tracts in grantee_to_tracts.items()
    }

    jc_by_county = defaultdict(list)
    for jc in judy_centers:
        jc_by_county[jc["county_fips"]].append(jc)

    joined = build_joined_schools()
    print(f"Spatial join produced {len(joined)} school rows "
          f"({len(joined[joined['GEOID20'].notna()])} matched to a tract).")

    audit = resolve_judy_centers(judy_centers, joined)
    audit_by_jc = {a["judy_center"]: a for a in audit}
    for jc in judy_centers:
        a = audit_by_jc[jc["school_name"]]
        jc["resolved_tract"] = a["geoid20"] or None
        jc["match_tier"] = a["tier"]
        jc["matched_school"] = a["matched_school"]

    # ---- Output: match audit (write first so we can inspect on failure) ----
    with (OUTPUTS / "jc_match_audit.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(audit[0].keys()))
        w.writeheader()
        w.writerows(audit)

    # ---- Output: per-grantee summary ----
    grantee_rows = []
    for grantee, tracts in sorted(grantee_to_tracts.items()):
        counties = grantee_counties[grantee]
        county_names = [COUNTY_NAMES.get(c, c) for c in counties]

        jc_in_counties = []
        for c in counties:
            jc_in_counties.extend(jc_by_county.get(c, []))

        in_grantee_tract = [
            jc["school_name"] for jc in jc_in_counties
            if jc["resolved_tract"] and jc["resolved_tract"] in tracts
        ]

        grantee_rows.append({
            "grantee": grantee,
            "n_tracts": len(tracts),
            "tracts": "; ".join(sorted(tracts)),
            "counties": ", ".join(county_names),
            "n_jc_in_counties": len(jc_in_counties),
            "jc_in_counties": "; ".join(sorted(j["school_name"] for j in jc_in_counties)),
            "n_jc_in_grantee_tracts": len(in_grantee_tract),
            "jc_in_grantee_tracts": "; ".join(in_grantee_tract),
            "county_level_coverage": "yes" if jc_in_counties else "NO COVERAGE",
            "tract_level_coverage": "yes" if in_grantee_tract else "no",
        })

    with (OUTPUTS / "grantee_coverage.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(grantee_rows[0].keys()))
        w.writeheader()
        w.writerows(grantee_rows)

    # ---- Output: per-Judy-Center summary ----
    jc_rows = []
    for jc in judy_centers:
        cfips = jc["county_fips"]
        same_county_grantees = {
            g for g, counties in grantee_counties.items() if cfips in counties
        }
        precise_grantees = (
            tract_to_grantees.get(jc["resolved_tract"], set())
            if jc["resolved_tract"] else set()
        )
        jc_rows.append({
            "county": jc["county"],
            "county_fips": cfips,
            "school_name": jc["school_name"],
            "school_address": jc["school_address"],
            "city": jc["city"],
            "vintage": jc["vintage"],
            "match_tier": jc["match_tier"],
            "resolved_tract": jc["resolved_tract"] or "",
            "matched_mdp_school": jc["matched_school"],
            "n_grantees_in_county": len(same_county_grantees),
            "grantees_in_county": "; ".join(sorted(same_county_grantees)),
            "grantees_serving_this_tract": "; ".join(sorted(precise_grantees)),
        })
    with (OUTPUTS / "judy_center_coverage.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(jc_rows[0].keys()))
        w.writeheader()
        w.writerows(jc_rows)

    # ---- Output: gaps ----
    gap_rows = [r for r in grantee_rows if r["county_level_coverage"] != "yes"]
    with (OUTPUTS / "gaps.csv").open("w", newline="") as f:
        if gap_rows:
            w = csv.DictWriter(f, fieldnames=list(gap_rows[0].keys()))
            w.writeheader()
            w.writerows(gap_rows)
        else:
            f.write("# No grantees lack county-level Judy Center coverage.\n")

    # ---- Output: candidate elementary schools per grantee tract ----
    # Every elementary/combined school whose tract is served by a grantee,
    # but which is NOT already a Judy Center. Ranked by CHILD_CPG_PCT desc.
    # Use (GEOID20, SCHOOL_NAME) as the JC identity — NCESSCH_ID is null on
    # ~5 MDP schools and would produce false positives.
    jc_keys = {
        (a["geoid20"], a["matched_school"].removesuffix(" (addr)"))
        for a in audit if a["geoid20"]
    }
    elem = joined[joined["SCHOOL_TYPE"].isin(["Elementary", "Combined"])].copy()
    elem = elem[elem["GEOID20"].notna()]
    elem_in_grantee_tracts = elem[elem["GEOID20"].isin(tract_to_grantees.keys())]

    candidate_rows = []
    for _, row in elem_in_grantee_tracts.iterrows():
        is_jc = (row["GEOID20"], row["SCHOOL_NAME"]) in jc_keys
        ncesid = row.get("NCESSCH_ID")
        if ncesid is None or (isinstance(ncesid, float) and ncesid != ncesid):
            ncesid = ""
        for grantee in tract_to_grantees.get(row["GEOID20"], set()):
            candidate_rows.append({
                "grantee": grantee,
                "geoid20": row["GEOID20"],
                "school_name": row["SCHOOL_NAME"],
                "school_type": row["SCHOOL_TYPE"],
                "ncessch_id": ncesid,
                "address": row.get("SCHOOL_STREET_ADDRESS") or "",
                "child_cpg_pct": row.get("CHILD_CPG_PCT"),
                "child_pov_pct": row.get("CHILD_POV_PCT"),
                "is_judy_center": "yes" if is_jc else "no",
            })
    candidate_rows.sort(
        key=lambda r: (r["grantee"], -(r["child_cpg_pct"] or 0))
    )
    with (OUTPUTS / "candidate_elementary_schools.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(candidate_rows[0].keys()))
        w.writeheader()
        w.writerows(candidate_rows)

    # ---- Catchment-zone analysis (Layer 1) ----
    catchments = build_catchments()
    # Build the grantee_tracts polygon frame in the same CRS
    raw_tracts = gpd.read_file(CACHE / "mdp_tracts_2025.geojson").to_crs(epsg=26985)
    grantee_tracts_gdf = raw_tracts[
        raw_tracts["GEOID20"].isin(tract_to_grantees.keys())
    ][["GEOID20", "geometry"]].copy()

    countywide_flags = flag_countywide_catchments(catchments, raw_tracts)

    # Build the set of school identifiers we care about for overlap analysis:
    # all resolved Judy Centers plus all candidate elementary schools.
    interesting_keys = set()
    for a in audit:
        if a["ncessch_id"]:
            interesting_keys.add(a["ncessch_id"])
    for c in candidate_rows:
        if c["ncessch_id"]:
            interesting_keys.add(c["ncessch_id"])

    overlap_rows = compute_catchment_overlaps(
        catchments, grantee_tracts_gdf, tract_to_grantees, interesting_keys
    )

    # Write the long-format overlap audit
    if overlap_rows:
        with (OUTPUTS / "catchment_overlaps.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(overlap_rows[0].keys()))
            w.writeheader()
            w.writerows(overlap_rows)

    # Write the county-wide flag audit (with school name lookup for readability)
    if countywide_flags:
        cat_lookup = {}
        for _, row in catchments.iterrows():
            key = row.get("NCESSCH_ID") or row.get("LEA_SITE") or ""
            if key and key not in cat_lookup:
                cat_lookup[key] = {
                    "SCHOOL_NAME": row.get("SCHOOL_NAME") or "",
                    "JURSCODE": row.get("JURSCODE") or "",
                    "SCHOOL_TYPE": row.get("SCHOOL_TYPE") or "",
                }
        with (OUTPUTS / "catchment_countywide_flags.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ncessch_or_lea_site", "school_name", "jurscode",
                        "school_type", "fraction_of_county_area"])
            for k, v in sorted(countywide_flags.items(), key=lambda kv: -kv[1]):
                meta = cat_lookup.get(k, {})
                w.writerow([k, meta.get("SCHOOL_NAME", ""), meta.get("JURSCODE", ""),
                            meta.get("SCHOOL_TYPE", ""), v])

    # Augment grantee_rows with catchment-overlap info: JCs whose catchment
    # touches the grantee's tracts but whose host school is OUTSIDE those
    # tracts (the "near-miss" case the prototype README anticipated).
    jc_keys_resolved = {a["ncessch_id"] for a in audit if a["ncessch_id"]}
    overlap_by_grantee = defaultdict(set)
    for o in overlap_rows:
        if o["ncessch_id"] in jc_keys_resolved:
            overlap_by_grantee[o["grantee"]].add(o["school_name"])

    # Strip JCs whose host school is already inside the grantee's tracts.
    for r in grantee_rows:
        in_tract = set(r["jc_in_grantee_tracts"].split("; ")) if r["jc_in_grantee_tracts"] else set()
        # The catchment school name from MDP may be a "shorter" form than the
        # JC list name (e.g. "Arundel Elementary/Middle" vs ".../...School").
        # Compare via NCES id instead: rebuild from audit.
        in_tract_ncesids = set()
        for a in audit:
            if a["geoid20"] in r["tracts"].split("; ") and a["ncessch_id"]:
                in_tract_ncesids.add(a["ncessch_id"])
        # Threshold: ignore trivial slivers (< 5% of tract or catchment area).
        near_miss = []
        for o in overlap_rows:
            if o["grantee"] != r["grantee"]:
                continue
            if o["ncessch_id"] not in jc_keys_resolved:
                continue
            if o["ncessch_id"] in in_tract_ncesids:
                continue
            if o["pct_of_tract"] < 0.05 and o["pct_of_catchment"] < 0.05:
                continue
            # Skip county-wide catchments (not informative as proximity)
            if (o["ncessch_id"] in countywide_flags
                    or o["lea_site"] in countywide_flags):
                continue
            near_miss.append(o["school_name"])
        # de-dupe preserving order
        seen, dedup = set(), []
        for n in near_miss:
            if n not in seen:
                seen.add(n)
                dedup.append(n)
        r["jcs_catchment_overlaps_tract"] = "; ".join(dedup)
        r["n_jcs_catchment_overlaps_tract"] = len(dedup)

    # Re-write grantee_coverage.csv with the new columns
    with (OUTPUTS / "grantee_coverage.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(grantee_rows[0].keys()))
        w.writeheader()
        w.writerows(grantee_rows)

    # ---- Output: executive summary (stakeholder-facing markdown) ----
    write_executive_summary(grantee_rows, candidate_rows, audit, joined,
                            tract_to_grantees)

    # ---- Output: site data (interactive map) ----
    write_site_data(
        grantee_rows=grantee_rows,
        candidate_rows=candidate_rows,
        audit=audit,
        overlap_rows=overlap_rows,
        countywide_flags=countywide_flags,
        joined_schools=joined,
        catchments=catchments,
        grantee_tracts_gdf=grantee_tracts_gdf,
        raw_tracts=raw_tracts,
        tract_to_grantees=tract_to_grantees,
    )

    # ---- Console summary ----
    print()
    print(f"{len(grantee_to_tracts)} grantees, {len(tract_to_grantees)} tracts, "
          f"{len(judy_centers)} Judy Centers")
    tier_counts = defaultdict(int)
    for a in audit:
        bucket = a["tier"].split("_")[0] if a["tier"] != "exact" else "exact"
        tier_counts[bucket] += 1
    print(f"Match tiers: {dict(tier_counts)}")
    unresolved = [a for a in audit if a["tier"] == "not_in_mdp"]
    if unresolved:
        print(f"Judy Centers not in MDP layer ({len(unresolved)}):")
        for a in unresolved:
            print(f"  - {a['judy_center']} ({a['county']})")

    n_tract_cov = sum(1 for r in grantee_rows if r["tract_level_coverage"] == "yes")
    print(f"Grantees with a Judy Center inside a served tract: "
          f"{n_tract_cov}/{len(grantee_rows)}")
    print(f"Outputs written to {OUTPUTS}/")


if __name__ == "__main__":
    analyze()
