"""
ENOUGH × Judy Center coverage prototype.

Inputs:
  - 2025_Updated_ENOUGH_Grantee_Census_Tracts_and_Zipcodes_-_Unique_ENOUGH_Census_Tracts__6_.csv
    (user-supplied: grantee → list of census tracts they serve)
  - judy_centers_full.csv
    (compiled from MSDE 2019 PDF + Dec 2025 PDF excerpts + LEA pages)

Outputs:
  - grantee_coverage.csv         per-grantee summary, one row per grantee
  - judy_center_coverage.csv     per-Judy-Center summary, one row per JC
  - gaps.csv                     grantees with no JC in their county (if any)

The tract-level spatial join is staged as a function and currently produces
NaN for the tract assignment because the MDP school-points layer cannot be
queried from this environment (web_fetch URL allowlist is too strict for
parameterised query URLs). The TODO in spatial_join_judy_centers_to_tracts()
shows the exact shape of the missing piece.
"""

import csv
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
GRANTEE_CSV = Path("/mnt/user-data/uploads/2025_Updated_ENOUGH_Grantee_Census_Tracts_and_Zipcodes_-_Unique_ENOUGH_Census_Tracts__6_.csv")
JUDY_CSV = HERE / "judy_centers_full.csv"

OUTPUTS = Path("/mnt/user-data/outputs")
OUTPUTS.mkdir(exist_ok=True)


# County FIPS → county name (Maryland 24xxx)
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


def load_grantees():
    """Returns (grantee_to_tracts dict, tract_to_grantees dict)."""
    grantee_to_tracts = defaultdict(set)
    tract_to_grantees = defaultdict(set)

    with GRANTEE_CSV.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            tract = row["ENOUGH Census Tract"].strip()
            # Up to two grantees per tract; the Grantee 1 / Grantee 2 split tells us.
            for col in ("Grantee 1", "Grantee 2"):
                g = (row.get(col) or "").strip()
                if g:
                    grantee_to_tracts[g].add(tract)
                    tract_to_grantees[tract].add(g)

    return grantee_to_tracts, tract_to_grantees


def load_judy_centers():
    """Returns list of dicts, one per Judy Center site."""
    rows = []
    with JUDY_CSV.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def tract_county_fips(tract_geoid):
    """First 5 chars of an 11-digit GEOID is the state+county FIPS."""
    return tract_geoid[:5]


def spatial_join_judy_centers_to_tracts(judy_centers, school_points_layer=None):
    """
    Resolve each Judy Center school to its census tract GEOID via point-in-polygon.

    Returns a dict: school_name -> tract_geoid (or None if unresolved).

    THIS IS THE STEP THAT CURRENTLY RETURNS NONE FOR EVERY ROW.
    To make it real, run this script in an environment that can reach
    https://mdpgis.mdp.state.md.us and replace the body with:

        import geopandas as gpd

        schools_url = (
            "https://mdpgis.mdp.state.md.us/arcgis/rest/services/"
            "Society/ENOUGH_Act_2025/FeatureServer/0/query"
            "?where=SCHOOL_TYPE+IN+('Elementary','Combined')"
            "&outFields=*&returnGeometry=true&f=geojson"
        )
        tracts_url = (
            "https://mdpgis.mdp.state.md.us/arcgis/rest/services/"
            "Society/ENOUGH_Act_2025/FeatureServer/2/query"
            "?where=1%3D1&outFields=GEOID20&returnGeometry=true&f=geojson"
        )
        schools = gpd.read_file(schools_url).to_crs(epsg=26985)
        tracts  = gpd.read_file(tracts_url ).to_crs(epsg=26985)
        joined  = gpd.sjoin(schools, tracts, predicate='within')

        out = {}
        for jc in judy_centers:
            row = joined[
                joined['SCHOOL_NAME'].str.strip().str.lower()
                == jc['school_name'].strip().lower()
            ]
            if not row.empty:
                out[jc['school_name']] = str(row.iloc[0]['GEOID20'])
        return out

    The two-layer download is small (~1400 schools, ~1400 tracts), and
    sjoin returns the tract for each school in a single pass.
    """
    return {jc["school_name"]: None for jc in judy_centers}


def analyze():
    grantee_to_tracts, tract_to_grantees = load_grantees()
    judy_centers = load_judy_centers()

    # tract → county fips, county fips → tracts
    tract_county = {t: tract_county_fips(t) for t in tract_to_grantees.keys()}
    grantee_counties = {
        g: sorted({tract_county[t] for t in tracts})
        for g, tracts in grantee_to_tracts.items()
    }

    # Group Judy Centers by county fips
    jc_by_county = defaultdict(list)
    for jc in judy_centers:
        jc_by_county[jc["county_fips"]].append(jc)

    # Run the spatial join (currently a stub — fills None)
    jc_to_tract = spatial_join_judy_centers_to_tracts(judy_centers)
    for jc in judy_centers:
        jc["resolved_tract"] = jc_to_tract.get(jc["school_name"])

    # ---- Output 1: per-grantee summary ----
    grantee_rows = []
    for grantee, tracts in sorted(grantee_to_tracts.items()):
        counties = grantee_counties[grantee]
        county_names = [COUNTY_NAMES.get(c, c) for c in counties]

        # All Judy Centers in any of this grantee's counties
        jc_in_counties = []
        for c in counties:
            jc_in_counties.extend(jc_by_county.get(c, []))

        # Hint heuristic: grantee names sometimes embed the locality or school
        # they target. Catches:
        #   "City of Cumberland"  -> South Penn (Cumberland)
        #   "One Annapolis"       -> Georgetown East (Annapolis)
        #   "San Mar ... Bester"  -> Bester Elementary
        #   "Frederick LMB"       -> all Frederick JCs (rough but useful)
        # We deliberately skip locality matches for "Baltimore" because it's
        # too coarse — every BC grantee would match every BC JC.
        SKIP_LOCALITY_TOKENS = {"baltimore"}
        grantee_lower = grantee.lower()
        same_city_match = []
        for jc in jc_in_counties:
            city = (jc.get("city") or "").lower().strip()
            school = jc.get("school_name", "").lower()

            # School-name token match: grab the distinctive first word of
            # the school (skipping titles like "The"/"Dr.") and look for it.
            school_tokens = [
                t for t in school.replace(",", " ").split()
                if t not in {"the", "dr.", "dr", "elementary", "school",
                             "middle", "primary", "early", "learning",
                             "center", "campus", "of", "and"}
            ]
            distinctive = school_tokens[0] if school_tokens else ""

            city_match = (
                city
                and city not in SKIP_LOCALITY_TOKENS
                and city in grantee_lower
            )
            school_match = (
                distinctive
                and len(distinctive) >= 5
                and distinctive in grantee_lower
            )
            if city_match or school_match:
                same_city_match.append(jc["school_name"])

        # Of those, any whose resolved_tract is in the grantee's tract list?
        # (Currently always empty until spatial_join is wired up.)
        in_grantee_tract = []
        for jc in jc_in_counties:
            if jc["resolved_tract"] and jc["resolved_tract"] in tracts:
                in_grantee_tract.append(jc["school_name"])

        grantee_rows.append({
            "grantee": grantee,
            "n_tracts": len(tracts),
            "tracts": "; ".join(sorted(tracts)),
            "counties": ", ".join(county_names),
            "n_jc_in_counties": len(jc_in_counties),
            "jc_in_counties": "; ".join(sorted(j["school_name"] for j in jc_in_counties)),
            "n_jc_in_grantee_tract_TRACT_LEVEL": len(in_grantee_tract),
            "jc_in_grantee_tract_TRACT_LEVEL": "; ".join(in_grantee_tract),
            "name_overlap_hint": "; ".join(same_city_match),
            "county_level_coverage": "yes" if jc_in_counties else "NO COVERAGE",
        })

    with (OUTPUTS / "grantee_coverage.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(grantee_rows[0].keys()))
        w.writeheader()
        w.writerows(grantee_rows)

    # ---- Output 2: per-Judy-Center summary ----
    jc_rows = []
    for jc in judy_centers:
        cfips = jc["county_fips"]
        # Grantees whose tracts are in the same county as this Judy Center
        same_county_grantees = set()
        for grantee, counties in grantee_counties.items():
            if cfips in counties:
                same_county_grantees.add(grantee)

        # If we had resolved_tract, this would tell us which grantee's
        # service area contains the Judy Center directly:
        precise_grantees = set()
        if jc["resolved_tract"]:
            precise_grantees = tract_to_grantees.get(jc["resolved_tract"], set())

        jc_rows.append({
            "county": jc["county"],
            "county_fips": cfips,
            "school_name": jc["school_name"],
            "school_address": jc["school_address"],
            "city": jc["city"],
            "vintage": jc["vintage"],
            "resolved_tract_TRACT_LEVEL": jc["resolved_tract"] or "(needs spatial join)",
            "n_grantees_in_county": len(same_county_grantees),
            "grantees_in_county": "; ".join(sorted(same_county_grantees)),
            "grantees_serving_this_tract_TRACT_LEVEL": "; ".join(sorted(precise_grantees)),
        })

    with (OUTPUTS / "judy_center_coverage.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(jc_rows[0].keys()))
        w.writeheader()
        w.writerows(jc_rows)

    # ---- Output 3: gap analysis ----
    gap_rows = [r for r in grantee_rows if r["county_level_coverage"] != "yes"]
    with (OUTPUTS / "gaps.csv").open("w", newline="") as f:
        if gap_rows:
            w = csv.DictWriter(f, fieldnames=list(gap_rows[0].keys()))
            w.writeheader()
            w.writerows(gap_rows)
        else:
            f.write("# No grantees lack county-level Judy Center coverage.\n")
            f.write("# Every Maryland county has at least one Judy Center per MSDE.\n")
            f.write("# The interesting gaps will appear at the TRACT level once the\n")
            f.write("# spatial join is wired in.\n")

    # ---- Console summary ----
    print(f"Loaded {len(grantee_to_tracts)} ENOUGH grantees covering "
          f"{len(tract_to_grantees)} unique tracts")
    print(f"Loaded {len(judy_centers)} Judy Center sites across "
          f"{len(jc_by_county)} counties")
    print()
    counties_with_grantees = set()
    for counties in grantee_counties.values():
        counties_with_grantees.update(counties)
    print(f"Grantees operate in {len(counties_with_grantees)} counties: "
          f"{sorted(COUNTY_NAMES.get(c, c) for c in counties_with_grantees)}")
    print()
    no_coverage = [r for r in grantee_rows if r["county_level_coverage"] != "yes"]
    print(f"Grantees with NO Judy Center in their county: {len(no_coverage)}")
    for r in no_coverage:
        print(f"  - {r['grantee']} ({r['counties']})")
    print()

    # Highlight name-overlap matches
    print("Name-overlap hints (high-likelihood spatial overlap):")
    for r in grantee_rows:
        if r["name_overlap_hint"]:
            print(f"  - {r['grantee']}")
            print(f"    -> {r['name_overlap_hint']}")
    print()

    print(f"Outputs written to {OUTPUTS}/")


if __name__ == "__main__":
    analyze()
