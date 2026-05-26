import os
import pandas as pd
import geopandas as gpd
from shapely.validation import make_valid

# Inputs
uploaded_csv = r"c:\Users\Ryan\Downloads\houston_metro_standard_zipcodes_zipdatamaps.csv"
uszips_csv = "uszips.csv"
msa_counties = {"Austin","Brazoria","Chambers","Fort Bend","Galveston","Harris","Liberty","Montgomery","Waller"}
zcta_zip_url = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_zcta520_500k.zip"

# Build ZIP sets
us = pd.read_csv(uszips_csv, dtype={"zip": str})
up = pd.read_csv(uploaded_csv, dtype={"zip_code": str})

tx = us[us["state_id"] == "TX"].copy()
tx["county_names_all"] = tx["county_names_all"].fillna("")
tx["counties"] = tx["county_names_all"].apply(lambda s: [x.strip() for x in s.split("|") if x.strip()])
tx["in_msa"] = tx["counties"].apply(lambda lst: any(c in msa_counties for c in lst))
my_prior = set(tx[(tx["zcta"] == True) & (~tx["military"].fillna(False)) & (tx["in_msa"])]["zip"])
csv_set = set(up["zip_code"].dropna().astype(str).str.zfill(5))
extras_22 = sorted(my_prior - csv_set)
universe = sorted(csv_set | set(extras_22))

# Read Census ZCTA polygons and filter
zcta = gpd.read_file(zcta_zip_url)
zip_col = "ZCTA5CE20" if "ZCTA5CE20" in zcta.columns else ("ZCTA5CE10" if "ZCTA5CE10" in zcta.columns else None)
if zip_col is None:
    raise RuntimeError(f"Could not find ZCTA code column. Found: {list(zcta.columns)}")

zcta[zip_col] = zcta[zip_col].astype(str).str.zfill(5)
sel = zcta[zcta[zip_col].isin(universe)].copy()

# Normalize geometries and project to equal-area CRS for exact area percentages
sel["geometry"] = sel["geometry"].make_valid()
sel = sel.to_crs(5070)
sel = sel[[zip_col, "geometry"]].rename(columns={zip_col: "zip"}).reset_index(drop=True)

found = set(sel["zip"])
missing_from_census = sorted(set(universe) - found)

# Precompute areas
sel["area_m2"] = sel.geometry.area

# Pairwise intersections (strict positive-area overlaps)
sidx = sel.sindex
pairs = []
for i, row in sel.iterrows():
    geom_i = row.geometry
    cand_idx = list(sidx.intersection(geom_i.bounds))
    for j in cand_idx:
        if j <= i:
            continue
        row_j = sel.iloc[j]
        geom_j = row_j.geometry
        if not geom_i.intersects(geom_j):
            continue
        inter = geom_i.intersection(geom_j)
        if inter.is_empty:
            continue
        inter_area = inter.area
        if inter_area <= 0:
            continue
        a_area = row.area_m2
        b_area = row_j.area_m2
        pairs.append({
            "zip_a": row.zip,
            "zip_b": row_j.zip,
            "intersection_area_m2": inter_area,
            "pct_of_zip_a": (inter_area / a_area) * 100 if a_area > 0 else 0,
            "pct_of_zip_b": (inter_area / b_area) * 100 if b_area > 0 else 0,
        })

pairs_df = pd.DataFrame(pairs)
if not pairs_df.empty:
    pairs_df = pairs_df.sort_values(["zip_a", "zip_b"]).reset_index(drop=True)

# 22-vs-CSV subset
extras_set = set(extras_22)
subset_df = pairs_df[((pairs_df["zip_a"].isin(extras_set)) & (pairs_df["zip_b"].isin(csv_set))) |
                     ((pairs_df["zip_b"].isin(extras_set)) & (pairs_df["zip_a"].isin(csv_set)))] if not pairs_df.empty else pd.DataFrame(columns=["zip_a","zip_b","intersection_area_m2","pct_of_zip_a","pct_of_zip_b"])

# Outputs
pairs_out = "zcta_overlap_pairs_all_254.csv"
subset_out = "zcta_overlap_pairs_22_vs_csv.csv"
missing_out = "zcta_missing_from_census_shapes.csv"
summary_out = "zcta_overlap_summary.txt"

pairs_df.to_csv(pairs_out, index=False)
subset_df.to_csv(subset_out, index=False)
pd.DataFrame({"zip": missing_from_census}).to_csv(missing_out, index=False)

with open(summary_out, "w", encoding="utf-8") as f:
    f.write(f"csv_count={len(csv_set)}\n")
    f.write(f"extras_22_count={len(extras_22)}\n")
    f.write(f"universe_count={len(universe)}\n")
    f.write(f"census_shapes_found={len(found)}\n")
    f.write(f"census_shapes_missing={len(missing_from_census)}\n")
    f.write(f"all_overlap_pairs_count={len(pairs_df)}\n")
    f.write(f"subset_22_vs_csv_overlap_pairs_count={len(subset_df)}\n")

print("extras_22:", ",".join(extras_22))
print(f"universe_count={len(universe)}")
print(f"census_shapes_found={len(found)}")
print(f"census_shapes_missing={len(missing_from_census)}")
if missing_from_census:
    print("missing_zips:", ",".join(missing_from_census))
print(f"all_overlap_pairs_count={len(pairs_df)}")
print(f"subset_22_vs_csv_overlap_pairs_count={len(subset_df)}")
print(f"wrote {pairs_out}, {subset_out}, {missing_out}, {summary_out}")
