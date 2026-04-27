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
    jc_ncesids = {a["ncessch_id"] for a in audit if a["ncessch_id"]}
    elem = joined[joined["SCHOOL_TYPE"].isin(["Elementary", "Combined"])].copy()
    elem = elem[elem["GEOID20"].notna()]
    elem_in_grantee_tracts = elem[elem["GEOID20"].isin(tract_to_grantees.keys())]

    candidate_rows = []
    for _, row in elem_in_grantee_tracts.iterrows():
        ncesid = row.get("NCESSCH_ID") or ""
        is_jc = ncesid in jc_ncesids
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
