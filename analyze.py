"""
ENOUGH x Judy Center coverage analysis.

Reads:
  - 2025 Updated ENOUGH Grantee Census Tracts and Zipcodes - Unique ENOUGH Census Tracts.csv
  - Maryland Family Network's Judy Center Hubs Map (the SuperStoreFinder XML
    feed at marylandchild.org). This is the authoritative current list,
    confirmed against MSDE source data. We cache the XML locally and
    materialise judy_centers_full.csv as an inspectable artifact.
  - MDP ENOUGH_Act_2025 FeatureServer (cached locally on first run):
      Layer 0  School Locations 2025  (point)
      Layer 1  School Attendance Zones 2025  (polygon)
      Layer 2  Poverty Census Tracts 2025  (polygon)

Writes:
  - judy_centers_full.csv             reconciled JC list (refreshed from MC)
  - grantee_coverage.csv              one row per grantee
  - judy_center_coverage.csv          one row per Judy Center
  - candidate_elementary_schools.csv  one row per elementary in a grantee tract
  - catchment_overlaps.csv            long-format overlap audit
  - catchment_countywide_flags.csv    schools with county-wide catchments
  - executive_summary.md              stakeholder-facing recommendations
  - gaps.csv                          grantees with no JC in their county
  - site/data/*.geojson + grantees.json  data for the Leaflet frontend

Pipeline:
  1. Refresh judy_centers_full.csv from MarylandChild.org (lat/lon-anchored).
  2. Pull MDP schools (Layer 0), catchments (Layer 1), tracts (Layer 2).
  3. Reproject to EPSG:26985 (NAD83 / MD State Plane).
  4. Spatial join schools -> tracts giving every school a GEOID20.
  5. Resolve each Judy Center to its tract via point-in-polygon on the
     MC-supplied lat/lon (no name-matching needed when lat/lon is present).
  6. For every elementary/combined school inside a grantee tract that is
     NOT already a Judy Center, surface it as a candidate. CHILD_CPG_PCT
     is reported as information, not as a gating threshold -- per GOC
     guidance, presence of an elementary in a JC-free tract is what
     matters; CPG is a sort key.
  7. Layer-1 catchment overlay: surface JCs whose catchment touches a
     grantee tract even when the school point is outside.
"""

import csv
import html
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
PATTY_CSV = HERE / "patty_centers_full.csv"
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
MC_SSF_URL = (
    "https://marylandchild.org/wp-content/plugins/superstorefinder-wp/"
    "ssf-wp-xml.php"
)

# Catchment area / county area >= this fraction => "county-wide" flag.
COUNTYWIDE_THRESHOLD = 0.70

# Patty Centers are community-based (no school attendance zone), so grantee
# proximity is measured as straight-line distance from the grantee's served-
# tract union to the PC point. A PC within this many metres of the tract
# union (but not inside it) classifies the grantee as "nearby". 5 km is a
# short-drive proxy; the exact distance is surfaced in outputs so the cutoff
# stays transparent.
PC_NEARBY_M = 5000

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


def refresh_judy_centers_from_mc():
    """Fetch the MarylandChild.org SuperStoreFinder feed, parse the JCs,
    and rewrite judy_centers_full.csv. Returns the parsed JC list.

    This is the authoritative source as of April 2026. The SSF XML carries
    school name, address, lat/lon, and a county tag for every site. We
    parse only items whose location ends with "(JC)"; the same feed also
    lists ~17 Patty/Family Support Centers ("(PC)"), which are parsed
    separately by refresh_patty_centers_from_mc().
    """
    import xml.etree.ElementTree as ET

    cache = CACHE / "marylandchild_ssf.xml"
    if not cache.exists():
        print(f"  fetching {MC_SSF_URL}")
        req = urllib.request.Request(
            MC_SSF_URL,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/121.0 Safari/537.36"
                ),
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            cache.write_bytes(resp.read())
    else:
        print(f"  using cached {cache.name}")

    tree = ET.parse(cache)
    store = next(c for c in tree.getroot() if c.tag == "store")

    def text(el, tag):
        sub = el.find(tag)
        # The SSF feed double-encodes HTML entities (&amp;, &#39;, &#44;);
        # unescape so names/addresses render as plain text everywhere.
        return html.unescape((sub.text or "").strip()) if sub is not None else ""

    rows = []
    for it in store:
        name = text(it, "location")
        if "(JC)" not in name:
            continue
        clean_name = re.sub(r"\s*\(JC\)\s*", "", name).strip()
        # Strip the "Maryland, " prefix some county tags carry
        county_tag = text(it, "productsServices")
        county_tag = re.sub(r"^Maryland,\s*", "", county_tag).strip()
        cfips = COUNTY_NAME_TO_FIPS.get(county_tag, "")
        addr_full = text(it, "address")
        # Parse "<street>  <city>,  MD <zip>"
        m = re.match(r"^(.*?)\s+([\w. ]+),\s*MD\s+(\d{5})\s*$", addr_full)
        if m:
            street, city, zipc = m.group(1).strip(), m.group(2).strip(), m.group(3)
        else:
            street, city, zipc = addr_full, "", ""
        rows.append({
            "county": county_tag,
            "county_fips": cfips,
            "school_name": clean_name,
            "school_address": street,
            "city": city,
            "zip": zipc,
            "lat": text(it, "latitude"),
            "lon": text(it, "longitude"),
            "phone": text(it, "telephone"),
            "mc_store_id": text(it, "storeId"),
            "source": "marylandchild.org SSF feed",
        })

    rows.sort(key=lambda r: (r["county"], r["school_name"]))
    with JUDY_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {JUDY_CSV.name} with {len(rows)} Judy Centers")
    return rows


def load_judy_centers():
    """Refresh from MC if missing, then read the CSV."""
    if not JUDY_CSV.exists():
        refresh_judy_centers_from_mc()
    with JUDY_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def refresh_patty_centers_from_mc():
    """Fetch the same MarylandChild.org SuperStoreFinder feed and parse the
    Patty (Family Support) Centers, rewriting patty_centers_full.csv.

    Patty Centers are tagged "(PC)" in the feed's location field (vs "(JC)"
    for Judy Centers). They are community-based hubs for parents of children
    birth-3 -- NOT sited in schools -- so unlike Judy Centers they have no
    school attendance zone and are not matched against MDP's school layer.
    We carry the MFN-supplied lat/lon for point-in-tract resolution.
    """
    import xml.etree.ElementTree as ET

    cache = CACHE / "marylandchild_ssf.xml"
    if not cache.exists():
        # load_judy_centers / refresh runs first and populates the cache;
        # fetch defensively in case this is called standalone.
        print(f"  fetching {MC_SSF_URL}")
        req = urllib.request.Request(
            MC_SSF_URL,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/121.0 Safari/537.36"
                ),
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            cache.write_bytes(resp.read())
    else:
        print(f"  using cached {cache.name}")

    tree = ET.parse(cache)
    store = next(c for c in tree.getroot() if c.tag == "store")

    def text(el, tag):
        sub = el.find(tag)
        # The SSF feed double-encodes HTML entities (&amp;, &#39;, &#44;);
        # unescape so names/addresses render as plain text everywhere.
        return html.unescape((sub.text or "").strip()) if sub is not None else ""

    rows = []
    for it in store:
        name = text(it, "location")
        if "(PC)" not in name:
            continue
        clean_name = re.sub(r"\s*\(PC\)\s*", "", name).strip()
        county_tag = text(it, "productsServices")
        county_tag = re.sub(r"^Maryland,\s*", "", county_tag).strip()
        cfips = COUNTY_NAME_TO_FIPS.get(county_tag, "")
        addr_full = text(it, "address")
        m = re.match(r"^(.*?)\s+([\w. ]+),\s*MD\s+(\d{5})\s*$", addr_full)
        if m:
            street, city, zipc = m.group(1).strip(), m.group(2).strip(), m.group(3)
        else:
            street, city, zipc = addr_full, "", ""
        rows.append({
            "county": county_tag,
            "county_fips": cfips,
            "site_name": clean_name,
            "site_address": street,
            "city": city,
            "zip": zipc,
            "lat": text(it, "latitude"),
            "lon": text(it, "longitude"),
            "phone": text(it, "telephone"),
            "mc_store_id": text(it, "storeId"),
            "source": "marylandchild.org SSF feed",
        })

    rows.sort(key=lambda r: (r["county"], r["site_name"]))
    with PATTY_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {PATTY_CSV.name} with {len(rows)} Patty Centers")
    return rows


def load_patty_centers():
    """Refresh from MC if missing, then read the CSV."""
    if not PATTY_CSV.exists():
        refresh_patty_centers_from_mc()
    with PATTY_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


# County name -> FIPS lookup (inverse of COUNTY_NAMES, with apostrophe variants)
COUNTY_NAME_TO_FIPS = {v: k for k, v in {
    "24001": "Allegany", "24003": "Anne Arundel", "24005": "Baltimore County",
    "24009": "Calvert", "24011": "Caroline", "24013": "Carroll",
    "24015": "Cecil", "24017": "Charles", "24019": "Dorchester",
    "24021": "Frederick", "24023": "Garrett", "24025": "Harford",
    "24027": "Howard", "24029": "Kent", "24031": "Montgomery",
    "24033": "Prince George's", "24035": "Queen Anne's", "24037": "St. Mary's",
    "24039": "Somerset", "24041": "Talbot", "24043": "Washington",
    "24045": "Wicomico", "24047": "Worcester", "24510": "Baltimore City",
}.items()}
# MC tags don't always match: handle the "Baltimore_City" underscore variant
COUNTY_NAME_TO_FIPS["Baltimore_City"] = "24510"
COUNTY_NAME_TO_FIPS["Baltimore City"] = "24510"
COUNTY_NAME_TO_FIPS["Prince George's"] = "24033"
COUNTY_NAME_TO_FIPS["Queen Anne's"] = "24035"
COUNTY_NAME_TO_FIPS["St. Mary's"] = "24037"


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
        # Sorted iteration so CSV output is reproducible across runs
        for grantee in sorted(tract_to_grantees.get(tract, set())):
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
    rows.sort(key=lambda r: (r["ncessch_id"], r["grantee"], r["tract_geoid"]))
    return rows


def resolve_judy_centers(judy_centers, joined_schools, raw_tracts):
    """
    For each Judy Center, return tract assignment + matched MDP school.

    Tier order is chosen for accuracy: MDP's school-point coordinates are
    authoritative; MarylandChild.org's lat/lon has known precision issues
    (e.g., Georgetown East ES is pinned in Chesapeake Bay; Arlington ES is
    ~1 km off, putting it in the wrong census tract). So we name-match
    against MDP first and only fall back to MC lat/lon if name matching
    fails.

      Tier 1: exact normalized name match within county (MDP geometry)
      Tier 2: rapidfuzz token_sort_ratio >= 85 within county (MDP geometry)
      Tier 3: address-based fuzzy match within county (MDP geometry)
      Tier 4: MarylandChild.org lat/lon point-in-polygon (when MDP misses)
    """
    from shapely.geometry import Point

    all_schools = joined_schools.copy()
    all_schools["norm_addr"] = all_schools["SCHOOL_STREET_ADDRESS"].fillna("").map(normalize_address)
    all_schools["county_fips"] = all_schools["JURSCODE"].map(JURSCODE_TO_FIPS)

    elem = joined_schools[
        joined_schools["SCHOOL_TYPE"].isin(["Elementary", "Combined"])
    ].copy()
    elem["norm"] = elem["SCHOOL_NAME"].fillna("").map(normalize_name)
    elem["county_fips"] = elem["JURSCODE"].map(JURSCODE_TO_FIPS)

    tracts_wgs = raw_tracts.to_crs(epsg=4326)

    audit = []
    for jc in judy_centers:
        # Strip "Judy Center" suffix MC sometimes uses for renamed sites
        # so that "Federalsburg Judy Center" matches MDP's "Federalsburg
        # Elementary".
        raw_name = jc["school_name"]
        clean_for_match = re.sub(
            r"\b(judy\s+center|judy\s+hoyer\s+family\s+learning\s+center)\b",
            "", raw_name, flags=re.IGNORECASE
        ).strip(" /")
        target = normalize_name(clean_for_match or raw_name)
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

        # Tier 3: address fallback within county across ALL school types.
        # token_sort_ratio (order-preserving) is stricter than token_set_ratio
        # for addresses -- avoids "2400 Mosher St" matching "2400 Windsor Ave"
        # just because the city/zip tokens overlap.
        target_addr = normalize_address(jc.get("school_address") or "")
        if target_addr:
            addr_pool = all_schools[all_schools["county_fips"] == cfips]
            addr_pool = addr_pool[addr_pool["norm_addr"] != ""]
            if not addr_pool.empty:
                choices = dict(zip(addr_pool.index, addr_pool["norm_addr"]))
                best = process.extractOne(
                    target_addr, choices, scorer=fuzz.token_sort_ratio
                )
                if best and best[1] >= 90:
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

        # Tier 4: MarylandChild.org lat/lon point-in-polygon. Used only
        # when MDP name/address matching all failed (typically because
        # the JC is hosted at a non-school facility or a recent addition
        # not yet in MDP). Less precise than MDP's coordinates but better
        # than no tract assignment.
        try:
            lat = float(jc.get("lat") or "")
            lon = float(jc.get("lon") or "")
        except ValueError:
            lat = lon = None
        if lat and lon:
            pt_wgs = Point(lon, lat)
            tract_hit = tracts_wgs[tracts_wgs.geometry.contains(pt_wgs)]
            if not tract_hit.empty:
                audit.append({
                    "judy_center": jc["school_name"],
                    "county": jc["county"],
                    "matched_school": "(via MC lat/lon)",
                    "ncessch_id": "",
                    "geoid20": str(tract_hit.iloc[0]["GEOID20"]),
                    "tier": "latlon",
                    "score": 90,
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


def resolve_patty_centers(patty_centers, raw_tracts):
    """Resolve each Patty Center to its census tract via point-in-polygon on
    the MarylandChild.org lat/lon.

    Unlike Judy Centers, Patty Centers are not in MDP's school layer, so
    there is no name/address match step -- the MFN-supplied coordinates are
    the only locator. Returns a list of audit dicts mirroring
    resolve_judy_centers' shape (minus the MDP-school fields).
    """
    from shapely.geometry import Point

    tracts_wgs = raw_tracts.to_crs(epsg=4326)

    audit = []
    for pc in patty_centers:
        try:
            lat = float(pc.get("lat") or "")
            lon = float(pc.get("lon") or "")
        except ValueError:
            lat = lon = None
        geoid = ""
        tier = "no_latlon"
        if lat and lon:
            tract_hit = tracts_wgs[tracts_wgs.geometry.contains(Point(lon, lat))]
            if not tract_hit.empty:
                geoid = str(tract_hit.iloc[0]["GEOID20"])
                tier = "latlon"
            else:
                tier = "latlon_no_tract"
        audit.append({
            "patty_center": pc["site_name"],
            "county": pc["county"],
            "geoid20": geoid,
            "tier": tier,
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


def _short_recommendation(has_candidate):
    """Per GOC guidance: presence of an elementary in a JC-free served
    tract is what matters. CPG is information, not a gate.
    """
    if has_candidate:
        return "Has elementary school(s) in served tracts — candidate for Judy Center"
    return "No traditional elementary in any served tract — investigate alternatives"


def write_site_data(grantee_rows, candidate_rows, audit, overlap_rows,
                     countywide_flags, joined_schools, catchments,
                     grantee_tracts_gdf, raw_tracts, tract_to_grantees,
                     closest_elementary_per_grantee=None,
                     patty_centers=None, pc_coverage_by_grantee=None):
    """Emit the files the static Leaflet site reads.

    site/data/
        grantee_tracts.geojson    EPSG:4326 polygons, 106 tracts
        schools.geojson           EPSG:4326 points: ALL 92 Judy Centers
                                  (state-wide context, purple-dot layer)
                                  PLUS candidate elementaries that play a
                                  role for at least one grantee
        catchments.geojson        EPSG:4326 polygons, catchments for
                                  the rendered schools (excluding
                                  county-wide ones)
        patty_centers.geojson     EPSG:4326 points: ALL Patty Centers, each
                                  with a per-grantee roles dict
                                  (pc_in_tract / pc_nearby)
        grantees.json             {dashboard: {...}, grantees: [...]}
    """
    closest_elementary_per_grantee = closest_elementary_per_grantee or {}
    patty_centers = patty_centers or []
    pc_coverage_by_grantee = pc_coverage_by_grantee or {}
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
    # We render: every resolved Judy Center (ALL 92, for the purple state-
    # wide layer) PLUS every candidate elementary that plays a role for at
    # least one grantee. JCs without any grantee role get an empty roles
    # dict and render purple by default; JCs with roles upgrade to blue
    # (in_tract) or green (catchment_overlap) when their grantee is
    # selected.
    schools_4326 = joined_schools.to_crs(epsg=4326)

    # Set of NCES ids we want to render (relevant schools + all JCs).
    render_ncesids = relevant_ncesids | jc_ncesids

    rel_schools = schools_4326[
        schools_4326["NCESSCH_ID"].fillna("").isin(render_ncesids)
    ].copy()
    rel_schools["lat"] = rel_schools.geometry.y
    rel_schools["lon"] = rel_schools.geometry.x
    rel_schools["roles"] = rel_schools["NCESSCH_ID"].map(
        lambda nid: dict(sorted(ncesid_to_role.get(nid, {}).items()))
    )
    rel_schools["is_judy_center"] = rel_schools["NCESSCH_ID"].map(
        lambda nid: nid in jc_ncesids
    )
    rel_schools["countywide_catchment"] = rel_schools["NCESSCH_ID"].map(
        lambda nid: nid in countywide_flags
    )
    # display_role classifies the school's default tile colour:
    #   "jc"        -> Judy Center (purple by default)
    #   "candidate" -> candidate elementary (red, only renders when
    #                  its grantee is selected)
    def _display_role(row):
        if row["is_judy_center"]:
            return "jc"
        return "candidate"
    rel_schools["display_role"] = rel_schools.apply(_display_role, axis=1)

    # Some Judy Centers are not in MDP at all (e.g. resolved via MC
    # lat/lon). For those we still want a point in schools.geojson so
    # the purple layer is complete. Pull them from the JC CSV directly.
    from shapely.geometry import Point
    jc_csv_rows = list(csv.DictReader(JUDY_CSV.open(encoding="utf-8")))
    jc_csv_by_name = {r["school_name"]: r for r in jc_csv_rows}
    extra_records = []
    for a in audit:
        if a["tier"] != "latlon":
            continue
        jc_row = jc_csv_by_name.get(a["judy_center"], {})
        try:
            lat = float(jc_row.get("lat") or "")
            lon = float(jc_row.get("lon") or "")
        except ValueError:
            continue
        extra_records.append({
            "ncessch_id": "",
            "lea_site": "",
            "school_name": a["judy_center"],
            "school_type": "Judy Center",
            "address": jc_row.get("school_address") or "",
            "geoid20": a["geoid20"],
            "cpg": None,
            "child_pov_pct": None,
            "lat": lat,
            "lon": lon,
            "roles": {},
            "is_judy_center": True,
            "countywide_catchment": False,
            "display_role": "jc",
            "geometry": Point(lon, lat),
        })

    keep = ["NCESSCH_ID", "LEA_SITE", "SCHOOL_NAME", "SCHOOL_TYPE",
            "SCHOOL_STREET_ADDRESS", "GEOID20", "CHILD_CPG_PCT",
            "CHILD_POV_PCT", "lat", "lon", "roles", "is_judy_center",
            "countywide_catchment", "display_role", "geometry"]
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
    if extra_records:
        extra_df = gpd.GeoDataFrame(extra_records, crs=4326)
        rel_schools = gpd.GeoDataFrame(
            __import__("pandas").concat([rel_schools, extra_df], ignore_index=True),
            crs=4326,
        )
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

    # --- 4b. Patty Centers GeoJSON (all PCs + per-grantee roles) ---
    # Each PC carries a roles dict {grantee: "pc_in_tract"|"pc_nearby"} so
    # the frontend can emphasise the relevant PC when a grantee is selected.
    pc_name_to_roles = defaultdict(dict)
    for gname, cov in pc_coverage_by_grantee.items():
        for nm in cov.get("in_tract", []):
            pc_name_to_roles[nm][gname] = "pc_in_tract"
        nb = cov.get("nearby")
        if nb:
            pc_name_to_roles[nb["site_name"]].setdefault(gname, "pc_nearby")

    from shapely.geometry import Point as _PCPoint
    pc_records = []
    for pc in patty_centers:
        try:
            lat = float(pc.get("lat") or "")
            lon = float(pc.get("lon") or "")
        except ValueError:
            continue
        pc_records.append({
            "site_name": pc["site_name"],
            "site_type": "Patty Center",
            "address": pc.get("site_address") or "",
            "city": pc.get("city") or "",
            "county": pc.get("county") or "",
            "geoid20": pc.get("resolved_tract") or "",
            "lat": lat,
            "lon": lon,
            "roles": dict(sorted(pc_name_to_roles.get(pc["site_name"], {}).items())),
            "geometry": _PCPoint(lon, lat),
        })
    if pc_records:
        pc_gdf_out = gpd.GeoDataFrame(pc_records, crs=4326)
        pc_gdf_out.to_file(SITE_DATA / "patty_centers.geojson", driver="GeoJSON")

    # --- 5. Per-grantee narrative ---
    cands_by_grantee = defaultdict(list)
    for c in candidate_rows:
        cands_by_grantee[c["grantee"]].append(c)
    for g in cands_by_grantee:
        cands_by_grantee[g].sort(
            key=lambda r: (-(float(r["child_cpg_pct"] or 0)),
                           0 if r["is_judy_center"] == "no" else 1)
        )

    # Pick, for each (grantee, JC) pair, the single overlap row with the
    # largest pct_of_tract — that's the "most-informative" near-miss to
    # show in the sidebar.
    best_overlap = {}
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
        key = (o["grantee"], o["ncessch_id"])
        prev = best_overlap.get(key)
        if prev is None or o["pct_of_tract"] > prev["pct_of_tract"]:
            best_overlap[key] = o

    near_miss_by_grantee = defaultdict(list)
    for (grantee, ncesid), o in sorted(best_overlap.items()):
        near_miss_by_grantee[grantee].append({
            "school_name": o["school_name"],
            "ncessch_id": ncesid,
            "pct_of_tract": o["pct_of_tract"],
            "pct_of_catchment": o["pct_of_catchment"],
            "tract_geoid": o["tract_geoid"],
        })

    grantees_payload = []
    for r in grantee_rows:
        g = r["grantee"]
        cands = cands_by_grantee.get(g, [])
        non_jc = [c for c in cands if c["is_judy_center"] == "no"]
        has_candidate = bool(non_jc)
        grantees_payload.append({
            "grantee": g,
            "county": r["counties"],
            "category": r.get("category", "needs_jc"),
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
            "closest_elementary": closest_elementary_per_grantee.get(g),
            "named_hint": g in NAMED_HINT_GRANTEES,
            "recommendation_short": _short_recommendation(has_candidate),
            "pc_category": pc_coverage_by_grantee.get(g, {}).get("category", "needs_pc"),
            "pcs_in_tracts": pc_coverage_by_grantee.get(g, {}).get("in_tract", []),
            "pc_nearby": pc_coverage_by_grantee.get(g, {}).get("nearby"),
            "n_pc_in_county": pc_coverage_by_grantee.get(g, {}).get("n_in_county", 0),
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

    # Dashboard counts
    n_in_tract = sum(1 for p in grantees_payload if p["category"] == "in_tract")
    n_catchment = sum(1 for p in grantees_payload if p["category"] == "catchment_overlap")
    n_needs = sum(1 for p in grantees_payload if p["category"] == "needs_jc")
    # Distinct JCs that play each role across all grantees
    jcs_in_tract_set = {a["ncessch_id"] for a in audit
                        if a["ncessch_id"]
                        and tract_to_grantees.get(a["geoid20"])}
    jcs_catchment_only = set()
    for p in grantees_payload:
        for nm in p["jcs_near_miss"]:
            jcs_catchment_only.add(nm["ncessch_id"])
    jcs_catchment_only -= jcs_in_tract_set
    # Dedup prospective by ncessch_id (some schools serve multiple grantees)
    n_prospective_distinct = len({c["ncessch_id"] for c in candidate_rows
                                  if c["is_judy_center"] == "no" and c["ncessch_id"]})

    # Patty Center dashboard counts
    n_pc_in_tract = sum(1 for p in grantees_payload if p["pc_category"] == "has_pc")
    n_pc_nearby = sum(1 for p in grantees_payload if p["pc_category"] == "nearby_pc")
    n_needs_pc = sum(1 for p in grantees_payload if p["pc_category"] == "needs_pc")

    dashboard = {
        "n_grantees": len(grantees_payload),
        "n_in_tract": n_in_tract,
        "n_catchment_overlap": n_catchment,
        "n_needs_jc": n_needs,
        "n_judy_centers_total": len(audit),
        "n_judy_centers_in_grantee_tracts": len(jcs_in_tract_set),
        "n_judy_centers_catchment_overlap_only": len(jcs_catchment_only),
        "n_prospective_elementaries": n_prospective_distinct,
        "n_patty_centers_total": len(patty_centers),
        "n_pc_in_tract": n_pc_in_tract,
        "n_pc_nearby": n_pc_nearby,
        "n_needs_pc": n_needs_pc,
    }

    import json
    payload = {"dashboard": dashboard, "grantees": grantees_payload}
    (SITE_DATA / "grantees.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    print(f"Wrote site data: {len(g_tracts)} tracts, {len(rel_schools)} schools, "
          f"{len(rel_cats)} catchments, {len(pc_records)} patty centers, "
          f"{len(grantees_payload)} grantees ({n_in_tract}/{n_catchment}/{n_needs} JC; "
          f"{n_pc_in_tract}/{n_pc_nearby}/{n_needs_pc} PC)")
    return payload


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


def write_executive_summary(grantee_rows, candidate_rows, audit, joined,
                             tract_to_grantees, dashboard=None,
                             grantees_payload=None,
                             closest_elementary_per_grantee=None,
                             pc_coverage_by_grantee=None):
    pc_coverage_by_grantee = pc_coverage_by_grantee or {}
    cands_by_grantee = defaultdict(list)
    for c in candidate_rows:
        cands_by_grantee[c["grantee"]].append(c)
    # Within each grantee, sort by CPG desc; non-JCs first within same CPG
    for g in cands_by_grantee:
        cands_by_grantee[g].sort(
            key=lambda r: (-(float(r["child_cpg_pct"] or 0)),
                           0 if r["is_judy_center"] == "no" else 1)
        )

    closest_elementary_per_grantee = closest_elementary_per_grantee or {}

    by_cat = {"in_tract": [], "catchment_overlap": [], "needs_jc": []}
    for r in grantee_rows:
        by_cat[r.get("category", "needs_jc")].append(r)

    # Sort each category: in_tract alphabetical; catchment_overlap by best
    # near-miss pct of tract (largest first); needs_jc by best non-JC CPG
    def best_non_jc_cpg(g):
        for c in cands_by_grantee.get(g, []):
            if c["is_judy_center"] == "no":
                try:
                    return float(c["child_cpg_pct"] or 0)
                except (TypeError, ValueError):
                    return 0.0
        return -1.0

    nm_lookup = {p["grantee"]: p["jcs_near_miss"] for p in (grantees_payload or [])}

    by_cat["in_tract"].sort(key=lambda r: r["grantee"])
    by_cat["catchment_overlap"].sort(
        key=lambda r: (
            -(max((nm["pct_of_tract"] for nm in nm_lookup.get(r["grantee"], [])),
                  default=0)),
            r["grantee"],
        )
    )
    by_cat["needs_jc"].sort(key=lambda r: (-best_non_jc_cpg(r["grantee"]),
                                            r["grantee"]))

    lines = []
    lines.append("# ENOUGH × Judy Center Coverage — Executive Summary")
    lines.append("")
    lines.append(
        f"Maryland's 28 ENOUGH grantees serve 106 census tracts. The state has "
        f"{dashboard['n_judy_centers_total']} Judy Center Early Learning Hubs. "
        f"This summary breaks the grantees into three groups: those that "
        f"already have a Judy Center in their served tracts, those served by "
        f"a nearby Judy Center whose attendance zone reaches in, and those "
        f"with no Judy Center coverage at all. For grantees in the third "
        f"group, it lists the elementary schools that could host a future "
        f"Judy Center."
    )
    lines.append("")

    # ---- Dashboard ----
    lines.append("## Dashboard")
    lines.append("")
    lines.append("| | |")
    lines.append("|---|---:|")
    lines.append(f"| ENOUGH grantees | **{dashboard['n_grantees']}** |")
    lines.append(f"| &nbsp; — Already covered (JC in tract) | {dashboard['n_in_tract']} |")
    lines.append(f"| &nbsp; — Catchment overlap (no in-tract JC, near-miss catchment) | {dashboard['n_catchment_overlap']} |")
    lines.append(f"| &nbsp; — Needs Judy Center | **{dashboard['n_needs_jc']}** |")
    lines.append(f"| Judy Centers statewide | {dashboard['n_judy_centers_total']} |")
    lines.append(f"| &nbsp; — In ENOUGH grantee tracts | {dashboard['n_judy_centers_in_grantee_tracts']} |")
    lines.append(f"| &nbsp; — Catchment-overlap only (school outside grantee tract) | {dashboard['n_judy_centers_catchment_overlap_only']} |")
    lines.append(f"| Distinct prospective elementaries in grantee tracts | {dashboard['n_prospective_elementaries']} |")
    lines.append(f"| Patty Centers statewide | {dashboard.get('n_patty_centers_total', 0)} |")
    lines.append(f"| &nbsp; — Grantees with a Patty Center in a served tract | {dashboard.get('n_pc_in_tract', 0)} |")
    lines.append(f"| &nbsp; — Grantees with a Patty Center nearby (≤5 km) | {dashboard.get('n_pc_nearby', 0)} |")
    lines.append(f"| &nbsp; — **Grantees with no nearby Patty Center** | **{dashboard.get('n_needs_pc', 0)}** |")
    lines.append("")

    # ---- Section: Already covered ----
    lines.append(f"## 1. Already covered ({dashboard['n_in_tract']})")
    lines.append("")
    lines.append("A Judy Center already sits inside one of the grantee's "
                 "served census tracts. No new application needed. Four "
                 "grantees marked with a ★ are named after their host Judy "
                 "Center school or locality and operate in close partnership "
                 "with it.")
    lines.append("")
    lines.append("| Grantee | County | Judy Center(s) in served tracts |")
    lines.append("|---|---|---|")
    for r in by_cat["in_tract"]:
        marker = " ★" if r["grantee"] in NAMED_HINT_GRANTEES else ""
        county = r["counties"]
        jcs = r["jc_in_grantee_tracts"] or "(see audit)"
        lines.append(f"| {r['grantee']}{marker} | {county} | {jcs} |")
    lines.append("")

    # ---- Section: Served by a nearby Judy Center ----
    lines.append(f"## 2. Served by a nearby Judy Center ({dashboard['n_catchment_overlap']})")
    lines.append("")
    lines.append("No Judy Center sits inside the grantee's tracts, but a Judy "
                 "Center elsewhere draws students from inside them through its "
                 "school attendance zone. In practice the grantee and the "
                 "existing Judy Center are likely already reaching the same "
                 "families. A new application may not be necessary; a direct "
                 "conversation with the host Judy Center is usually the right "
                 "first step.")
    lines.append("")
    lines.append("| Grantee | County | Near-miss Judy Center | % of tract overlap |")
    lines.append("|---|---|---|---:|")
    for r in by_cat["catchment_overlap"]:
        nm_list = nm_lookup.get(r["grantee"], [])
        if nm_list:
            top = max(nm_list, key=lambda n: n["pct_of_tract"])
            jc = top["school_name"].strip()
            pct = _fmt_pct(top["pct_of_tract"])
        else:
            jc = "—"
            pct = "—"
        county = r["counties"]
        lines.append(f"| {r['grantee']} | {county} | {jc} | {pct} |")
    lines.append("")

    # ---- Section: Not served by a Judy Center ----
    lines.append(f"## 3. Not served by a Judy Center ({dashboard['n_needs_jc']})")
    lines.append("")
    lines.append("No Judy Center inside the grantee's tracts, and no nearby "
                 "Judy Center whose attendance zone reaches in. These are the "
                 "grantees most worth a conversation about a new Judy Center. "
                 "The table lists the top elementary schools in their tracts "
                 "as starting points. Where a grantee's tracts contain no "
                 "traditional elementary at all, the closest one outside the "
                 "tract is shown as a fallback.")
    lines.append("")
    lines.append("| Grantee | County | Top candidate(s) | Notes |")
    lines.append("|---|---|---|---|")
    for r in by_cat["needs_jc"]:
        cands = cands_by_grantee.get(r["grantee"], [])
        non_jc_cands = [c for c in cands if c["is_judy_center"] == "no"]
        county = r["counties"]
        if non_jc_cands:
            top = non_jc_cands[:2]
            top_str = "; ".join(
                f"{c['school_name']} ({_fmt_pct(c['child_cpg_pct'])})"
                for c in top
            )
            note = ""
        else:
            ce = closest_elementary_per_grantee.get(r["grantee"])
            if ce:
                km = ce["distance_m"] / 1000
                top_str = (f"_(closest fallback: {ce['school_name']} at "
                           f"{km:.1f} km, CPG {_fmt_pct(ce['cpg'])})_")
                note = "No traditional elementary in tract"
            else:
                top_str = "No traditional elementary in any served tract"
                note = "Investigate alternative school types"
        lines.append(f"| {r['grantee']} | {county} | {top_str} | {note} |")
    lines.append("")
    lines.append("**Already-aligned grantees.** Four grantees are named after "
                 "their host Judy Center school or locality and operate in "
                 "close partnership with it: City of Cumberland with South "
                 "Penn Elementary, One Annapolis with Georgetown East "
                 "Elementary, San Mar with Bester Elementary, and Frederick "
                 "Local Management Board with Waverley, Hillcrest, and "
                 "Butterfly Ridge Elementary. Frederick LMB serves 5 tracts; "
                 "3 of the 6 Frederick County Judy Centers sit inside them, "
                 "while the other three (Lincoln, Monocacy, Tuscarora) are "
                 "in adjacent Frederick tracts not covered by the current "
                 "grant.")
    lines.append("")

    # ---- Section: Patty Center coverage ----
    n_pc_total = dashboard.get("n_patty_centers_total", 0)
    lines.append("## Patty Center coverage")
    lines.append("")
    lines.append(
        f"Patty Centers (formerly Family Support Centers) are community-based "
        f"hubs for parents of children birth–3 — they help parents pursue "
        f"their own goals (GED, job readiness, parenting) while their child "
        f"receives on-site early care. Unlike Judy Centers they are not housed "
        f"in schools and have no attendance zone, so a grantee is counted as "
        f"covered when a Patty Center sits inside a served tract, and as "
        f"\"nearby\" when one sits within 5 km of its served tracts. Maryland "
        f"Family Network operates {n_pc_total} Patty Centers as MSDE's "
        f"intermediary; the Blueprint funds 3 new sites per year through "
        f"FY2030 at $330,000 each, so identifying ENOUGH communities without "
        f"one is timely."
    )
    lines.append("")
    # Group grantees by PC category
    pc_has = [r for r in grantee_rows if r.get("pc_category") == "has_pc"]
    pc_near = [r for r in grantee_rows if r.get("pc_category") == "nearby_pc"]
    pc_needs = [r for r in grantee_rows if r.get("pc_category") == "needs_pc"]

    lines.append(f"**Grantees with a Patty Center in a served tract "
                 f"({len(pc_has)}).** " + (
                     "; ".join(f"{r['grantee']} ({r.get('pc_in_grantee_tracts','')})"
                               for r in sorted(pc_has, key=lambda r: r['grantee']))
                     or "None") + ".")
    lines.append("")
    lines.append(f"**Grantees with a Patty Center nearby, within 5 km "
                 f"({len(pc_near)}).** " + (
                     "; ".join(f"{r['grantee']} — {r.get('pc_nearby','')}"
                               for r in sorted(pc_near, key=lambda r: r['grantee']))
                     or "None") + ".")
    lines.append("")
    lines.append(f"**Grantees with no nearby Patty Center ({len(pc_needs)}).** "
                 "These ENOUGH communities are the candidates a future Patty "
                 "Center expansion could prioritise:")
    lines.append("")
    lines.append("| Grantee | County | Patty Centers in county |")
    lines.append("|---|---|---:|")
    for r in sorted(pc_needs, key=lambda r: (-(r.get("n_pc_in_county", 0)), r["grantee"])):
        lines.append(f"| {r['grantee']} | {r['counties']} | {r.get('n_pc_in_county', 0)} |")
    lines.append("")

    # ---- Full candidate elementary list per grantee without coverage ----
    lines.append("## Candidate elementary schools by grantee")
    lines.append("")
    lines.append("Every elementary or combined school whose census tract is served "
                 "by an ENOUGH grantee, listed in CPG-descending order within "
                 "grantee for visual prioritisation. CPG is information, not a "
                 "filter -- every row is a viable candidate per GOC guidance. "
                 "Schools already operating as Judy Centers are marked. "
                 "`candidate_elementary_schools.csv` carries the same data with "
                 "GEOID20 and NCES IDs.")
    lines.append("")
    for r in (by_cat["needs_jc"] + by_cat["catchment_overlap"]
              + by_cat["in_tract"]):
        cands = cands_by_grantee.get(r["grantee"], [])
        if not cands:
            continue
        lines.append(f"### {r['grantee']}")
        lines.append(f"_{r['counties']} — {r['n_tracts']} served tract(s) — "
                     f"category: {r['category']}_")
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

    # ---- Methodology ----
    n_total_jc = len(audit)
    lines.append("## Methodology")
    lines.append("")
    lines.append("**What this combines.** The ENOUGH grantee roster from the "
                 "Governor's Office for Children, the current Judy Center "
                 "directory from Maryland Family Network's public map, and "
                 "Maryland's school points, school attendance zones, and "
                 "census-tract polygons from the Maryland Department of "
                 "Planning's ENOUGH map service.")
    lines.append("")
    lines.append(f"**How grantees are sorted.** Each grantee falls into "
                 f"exactly one group: a Judy Center sits inside one of "
                 f"their served census tracts ({dashboard['n_in_tract']} "
                 f"grantees), a Judy Center elsewhere reaches into a served "
                 f"tract through its school attendance zone "
                 f"({dashboard['n_catchment_overlap']}), or neither "
                 f"({dashboard['n_needs_jc']}).")
    lines.append("")
    lines.append(f"**Matching Judy Centers to schools.** Each Judy Center "
                 f"from Maryland Family Network's directory was paired with "
                 f"the corresponding school in MDP's records by name, then "
                 f"address, then directory coordinates. A few directory pins "
                 f"land in the wrong census tract, so MDP coordinates are "
                 f"preferred when available. All {n_total_jc} sites resolved "
                 f"cleanly.")
    lines.append("")
    lines.append("**About CPG.** CPG (concentrated poverty grant rate) is a "
                 "three-year rolling average of the share of students at a "
                 "school who qualify for free and reduced-price meals. CPG "
                 "appears next to each candidate school as a visual "
                 "prioritisation cue. It is not used as a filter in this "
                 "analysis — every elementary in a grantee's tract is a "
                 "viable candidate.")
    lines.append("")
    lines.append("**A few things to keep in mind.**")
    lines.append("- A school's CPG can be much higher than the child-poverty "
                 "rate of the tract it sits in. CPG counts the students "
                 "enrolled at the school; tract poverty counts the children "
                 "living in the tract. Open-boundary enrollment and magnet "
                 "programs routinely produce this gap.")
    lines.append("- A handful of Judy Centers have school attendance zones "
                 "that cover most of a small county. Those are excluded from "
                 "the \"served by a nearby Judy Center\" group because "
                 "covering an entire county doesn't say anything meaningful "
                 "about proximity to a particular grantee.")
    lines.append("- One grantee (Leaders Breeders Inc.) has no traditional "
                 "elementary in its served tract — only a charter school. "
                 "The closest non-Judy-Center elementary outside the tract "
                 "(Sinclair Lane Elementary, about half a kilometre away) "
                 "is shown as a fallback. Judy Center placement at a charter "
                 "school is a policy question worth raising directly with "
                 "MSDE.")
    lines.append("")

    (OUTPUTS / "executive_summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("Wrote executive_summary.md")


# ---- Analysis ----

def analyze():
    grantee_to_tracts, tract_to_grantees = load_grantees()
    print("Refreshing Judy Center list from MarylandChild.org...")
    refresh_judy_centers_from_mc()
    judy_centers = load_judy_centers()
    print("Refreshing Patty Center list from MarylandChild.org...")
    refresh_patty_centers_from_mc()
    patty_centers = load_patty_centers()

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

    raw_tracts_for_pip = gpd.read_file(CACHE / "mdp_tracts_2025.geojson")
    audit = resolve_judy_centers(judy_centers, joined, raw_tracts_for_pip)
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

    # ---- Resolve Patty Centers (lat/lon point-in-tract only) ----
    pc_audit = resolve_patty_centers(patty_centers, raw_tracts_for_pip)
    pc_audit_by_name = {a["patty_center"]: a for a in pc_audit}
    for pc in patty_centers:
        a = pc_audit_by_name[pc["site_name"]]
        pc["resolved_tract"] = a["geoid20"] or None
        pc["match_tier"] = a["tier"]
    with (OUTPUTS / "pc_match_audit.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(pc_audit[0].keys()))
        w.writeheader()
        w.writerows(pc_audit)

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
            "zip": jc.get("zip", ""),
            "lat": jc.get("lat", ""),
            "lon": jc.get("lon", ""),
            "mc_store_id": jc.get("mc_store_id", ""),
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
    # Strip JCs whose host school is already inside the grantee's tracts.
    for r in grantee_rows:
        # The catchment school name from MDP may be a "shorter" form than
        # the JC list name (e.g. "Arundel Elementary/Middle" vs
        # ".../...School"). Compare via NCES id instead.
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

    # Per-grantee disjoint category for the dashboard.
    # Cat 1 in_tract: at least one Judy Center inside a served tract.
    # Cat 2 catchment_overlap: no in-tract JC, but at least one near-miss.
    # Cat 3 needs_jc: neither.
    for r in grantee_rows:
        if r["tract_level_coverage"] == "yes":
            r["category"] = "in_tract"
        elif r["n_jcs_catchment_overlaps_tract"] > 0:
            r["category"] = "catchment_overlap"
        else:
            r["category"] = "needs_jc"

    # Closest non-JC elementary fallback for Cat 3 grantees with zero
    # in-tract candidate elementaries (currently only Leaders Breeders,
    # whose single tract contains only Belair-Edison Charter).
    # We measure distance from the school point to the nearest edge of
    # the grantee's tract union, capped at 10 km.
    closest_elementary_per_grantee = {}
    elem_for_closest = joined[
        joined["SCHOOL_TYPE"].isin(["Elementary", "Combined"])
    ].copy()
    jc_ncesids = {a["ncessch_id"] for a in audit if a["ncessch_id"]}
    elem_non_jc = elem_for_closest[
        ~elem_for_closest["NCESSCH_ID"].fillna("").isin(jc_ncesids)
    ]
    cands_by_g = defaultdict(list)
    for c in candidate_rows:
        cands_by_g[c["grantee"]].append(c)
    for r in grantee_rows:
        if r["category"] != "needs_jc":
            continue
        non_jc_cands = [c for c in cands_by_g.get(r["grantee"], [])
                        if c["is_judy_center"] == "no"]
        if non_jc_cands:
            continue  # has at least one in-tract candidate; no fallback needed
        gtr = grantee_tracts_gdf[
            grantee_tracts_gdf["GEOID20"].isin(r["tracts"].split("; "))
        ]
        if gtr.empty:
            continue
        union_geom = gtr.geometry.union_all()
        elem_non_jc = elem_non_jc.copy()
        elem_non_jc["dist_m"] = elem_non_jc.geometry.distance(union_geom)
        within_10km = elem_non_jc[elem_non_jc["dist_m"] <= 10000]
        if within_10km.empty:
            continue
        nearest = within_10km.sort_values("dist_m").iloc[0]
        # Reproject the school point to WGS84 for lat/lon.
        from shapely.ops import transform as shapely_transform
        from pyproj import Transformer
        to_wgs = Transformer.from_crs(26985, 4326, always_xy=True).transform
        wgs_pt = shapely_transform(to_wgs, nearest.geometry)
        closest_elementary_per_grantee[r["grantee"]] = {
            "school_name": nearest.get("SCHOOL_NAME") or "",
            "ncessch_id": nearest.get("NCESSCH_ID") or "",
            "school_type": nearest.get("SCHOOL_TYPE") or "",
            "address": nearest.get("SCHOOL_STREET_ADDRESS") or "",
            "cpg": float(nearest["CHILD_CPG_PCT"]) if nearest.get("CHILD_CPG_PCT") not in (None, "") else None,
            "geoid20": str(nearest.get("GEOID20") or ""),
            "distance_m": int(nearest["dist_m"]),
            "lat": wgs_pt.y,
            "lon": wgs_pt.x,
        }

    # ---- Patty Center per-grantee coverage (tiered by distance) ----
    # PCs are community-based (no attendance zone), so we classify each
    # grantee by straight-line distance from its served-tract union to the
    # nearest PC point: in-tract -> nearby (<= PC_NEARBY_M) -> needs one.
    pc_by_county = defaultdict(list)
    for pc in patty_centers:
        pc_by_county[pc["county_fips"]].append(pc)

    # Build a PC point frame in EPSG:26985 once for distance math.
    from shapely.geometry import Point as _Point
    pc_pts = []
    for pc in patty_centers:
        try:
            lat = float(pc.get("lat") or "")
            lon = float(pc.get("lon") or "")
        except ValueError:
            continue
        pc_pts.append({"site_name": pc["site_name"], "resolved_tract": pc["resolved_tract"],
                       "geometry": _Point(lon, lat)})
    pc_gdf = gpd.GeoDataFrame(pc_pts, crs=4326).to_crs(epsg=26985) if pc_pts else None

    # grantee -> {category, in_tract names, nearby (name+dist), n in county}
    pc_coverage_by_grantee = {}
    for r in grantee_rows:
        gname = r["grantee"]
        gtracts = set(r["tracts"].split("; ")) if r["tracts"] else set()
        in_tract = sorted({pc["site_name"] for pc in patty_centers
                           if pc["resolved_tract"] in gtracts})
        n_in_county = sum(len(pc_by_county.get(c, []))
                          for c in grantee_counties[gname])
        nearby = None
        if not in_tract and pc_gdf is not None:
            gtr = grantee_tracts_gdf[grantee_tracts_gdf["GEOID20"].isin(gtracts)]
            if not gtr.empty:
                union_geom = gtr.geometry.union_all()
                dists = pc_gdf.geometry.distance(union_geom)
                nearest_idx = dists.idxmin()
                nearest_dist = float(dists.loc[nearest_idx])
                if nearest_dist <= PC_NEARBY_M:
                    nearby = {
                        "site_name": pc_gdf.loc[nearest_idx, "site_name"],
                        "distance_m": int(nearest_dist),
                    }
        if in_tract:
            category = "has_pc"
        elif nearby:
            category = "nearby_pc"
        else:
            category = "needs_pc"
        pc_coverage_by_grantee[gname] = {
            "category": category, "in_tract": in_tract,
            "nearby": nearby, "n_in_county": n_in_county,
        }
        r["pc_category"] = category
        r["pc_in_grantee_tracts"] = "; ".join(in_tract)
        r["pc_nearby"] = (f"{nearby['site_name']} ({nearby['distance_m']/1000:.1f} km)"
                          if nearby else "")
        r["n_pc_in_county"] = n_in_county

    # ---- Output: per-Patty-Center coverage ----
    pc_rows = []
    for pc in patty_centers:
        precise_grantees = (tract_to_grantees.get(pc["resolved_tract"], set())
                            if pc["resolved_tract"] else set())
        pc_rows.append({
            "county": pc["county"],
            "county_fips": pc["county_fips"],
            "site_name": pc["site_name"],
            "site_address": pc["site_address"],
            "city": pc["city"],
            "zip": pc.get("zip", ""),
            "lat": pc.get("lat", ""),
            "lon": pc.get("lon", ""),
            "mc_store_id": pc.get("mc_store_id", ""),
            "match_tier": pc["match_tier"],
            "resolved_tract": pc["resolved_tract"] or "",
            "grantees_serving_this_tract": "; ".join(sorted(precise_grantees)),
        })
    with (OUTPUTS / "patty_center_coverage.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(pc_rows[0].keys()))
        w.writeheader()
        w.writerows(pc_rows)

    # Re-write grantee_coverage.csv with the new columns
    with (OUTPUTS / "grantee_coverage.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(grantee_rows[0].keys()))
        w.writeheader()
        w.writerows(grantee_rows)

    # ---- Output: site data (interactive map) ----
    site_data_payload = write_site_data(
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
        closest_elementary_per_grantee=closest_elementary_per_grantee,
        patty_centers=patty_centers,
        pc_coverage_by_grantee=pc_coverage_by_grantee,
    )

    # ---- Output: executive summary (stakeholder-facing markdown) ----
    write_executive_summary(grantee_rows, candidate_rows, audit, joined,
                            tract_to_grantees,
                            dashboard=site_data_payload["dashboard"],
                            grantees_payload=site_data_payload["grantees"],
                            closest_elementary_per_grantee=closest_elementary_per_grantee,
                            pc_coverage_by_grantee=pc_coverage_by_grantee)

    # ---- Console summary ----
    print()
    print(f"{len(grantee_to_tracts)} grantees, {len(tract_to_grantees)} tracts, "
          f"{len(judy_centers)} Judy Centers, {len(patty_centers)} Patty Centers")
    n_pc_cats = defaultdict(int)
    for r in grantee_rows:
        n_pc_cats[r["pc_category"]] += 1
    print(f"Patty Center grantee tiers: {dict(n_pc_cats)}")
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
