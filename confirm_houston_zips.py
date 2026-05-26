#!/usr/bin/env python3
"""
Build a 1 mile by 1 mile centroid grid for the Houston ZIP-defined area.

The Houston area is defined by the ZIP codes listed in houston_zips.csv. The
ZIP boundary geometry comes from the filtered Census ZCTA shapefile already
stored in preprocessing/houston-zip-shapefiles.

The output CSV is meant to be the location table for grid-based ingestion:
one row per grid square whose centroid falls inside one of the selected ZIP
polygons.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


MILE_IN_METERS = 1609.344
EARTH_RADIUS_METERS = 6_371_008.8

DEFAULT_ZIP_CSV_CANDIDATES = (
    "greater_houston_confirmed_census_zctas.csv",
    "houston_zips.csv",
    "houston_zip.csv",
)
DEFAULT_ZCTA_SHAPEFILE = Path("preprocessing/houston-zip-shapefiles/houston_zcta_filtered.shp")
DEFAULT_OUTPUT = Path("static data/houston_grid_centroids.csv")
DEFAULT_ZIP_FIELDS = ("ZCTA5CE20", "ZCTA5CE10", "GEOID20", "GEOID10", "zip", "zcta")


@dataclass(frozen=True)
class ShapeRecord:
    zip_code: str
    rings_lon_lat: list[list[tuple[float, float]]]


@dataclass(frozen=True)
class ProjectedShape:
    zip_code: str
    rings_xy: list[list[tuple[float, float]]]
    bbox: tuple[float, float, float, float]


class LocalAzimuthalEquidistant:
    """Small, dependency-free local projection for mile-sized grid cells."""

    def __init__(self, origin_lat: float, origin_lon: float):
        self.origin_lat = math.radians(origin_lat)
        self.origin_lon = math.radians(origin_lon)
        self.sin_origin_lat = math.sin(self.origin_lat)
        self.cos_origin_lat = math.cos(self.origin_lat)

    def forward(self, lat: float, lon: float) -> tuple[float, float]:
        lat_rad = math.radians(lat)
        lon_rad = math.radians(lon)
        delta_lon = _normalize_radians(lon_rad - self.origin_lon)

        sin_lat = math.sin(lat_rad)
        cos_lat = math.cos(lat_rad)
        cos_delta_lon = math.cos(delta_lon)

        cos_c = (
            self.sin_origin_lat * sin_lat
            + self.cos_origin_lat * cos_lat * cos_delta_lon
        )
        cos_c = max(-1.0, min(1.0, cos_c))
        c = math.acos(cos_c)

        if abs(c) < 1e-12:
            scale = 1.0
        else:
            scale = c / math.sin(c)

        x = EARTH_RADIUS_METERS * scale * cos_lat * math.sin(delta_lon)
        y = EARTH_RADIUS_METERS * scale * (
            self.cos_origin_lat * sin_lat
            - self.sin_origin_lat * cos_lat * cos_delta_lon
        )
        return x, y

    def inverse(self, x: float, y: float) -> tuple[float, float]:
        rho = math.hypot(x, y)
        if rho < 1e-12:
            return math.degrees(self.origin_lat), math.degrees(self.origin_lon)

        c = rho / EARTH_RADIUS_METERS
        sin_c = math.sin(c)
        cos_c = math.cos(c)

        lat = math.asin(
            cos_c * self.sin_origin_lat
            + (y * sin_c * self.cos_origin_lat / rho)
        )
        lon = self.origin_lon + math.atan2(
            x * sin_c,
            rho * self.cos_origin_lat * cos_c
            - y * self.sin_origin_lat * sin_c,
        )
        return math.degrees(lat), math.degrees(_normalize_radians(lon))


def _normalize_radians(value: float) -> float:
    return (value + math.pi) % (2.0 * math.pi) - math.pi


def normalize_zip(value: object) -> str | None:
    if value is None:
        return None
    match = re.search(r"\d{5}", str(value))
    if not match:
        return None
    return match.group(0)


def resolve_default_zip_csv() -> Path:
    for candidate in DEFAULT_ZIP_CSV_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return path
    return Path(DEFAULT_ZIP_CSV_CANDIDATES[0])


def load_zip_codes(csv_path: Path, zip_column: str) -> set[str]:
    with csv_path.open("r", newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames:
            raise ValueError(f"{csv_path} does not have a header row.")
        if zip_column not in reader.fieldnames:
            raise ValueError(
                f"{csv_path} does not contain column '{zip_column}'. "
                f"Available columns: {reader.fieldnames}"
            )

        zips = {
            zip_code
            for row in reader
            if (zip_code := normalize_zip(row.get(zip_column))) is not None
        }

    if not zips:
        raise ValueError(f"No ZIP codes found in {csv_path}.")
    return zips


def load_projection_origin(csv_path: Path) -> tuple[float, float]:
    latitudes: list[float] = []
    longitudes: list[float] = []
    with csv_path.open("r", newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            try:
                latitudes.append(float(row["latitude"]))
                longitudes.append(float(row["longitude"]))
            except (KeyError, TypeError, ValueError):
                continue

    if not latitudes or not longitudes:
        return 29.7604, -95.3698
    return sum(latitudes) / len(latitudes), sum(longitudes) / len(longitudes)


def read_dbf_records(dbf_path: Path) -> tuple[list[str], list[dict[str, str]]]:
    data = dbf_path.read_bytes()
    if len(data) < 33:
        raise ValueError(f"{dbf_path} is not a valid DBF file.")

    record_count = struct.unpack("<I", data[4:8])[0]
    header_length = struct.unpack("<H", data[8:10])[0]
    record_length = struct.unpack("<H", data[10:12])[0]

    fields: list[tuple[str, int]] = []
    offset = 32
    while offset + 32 <= len(data) and data[offset] != 0x0D:
        descriptor = data[offset : offset + 32]
        raw_name = descriptor[:11].split(b"\x00", 1)[0]
        name = raw_name.decode("ascii", errors="ignore").strip()
        length = descriptor[16]
        if name:
            fields.append((name, length))
        offset += 32

    records: list[dict[str, str]] = []
    position = header_length
    for _ in range(record_count):
        raw_record = data[position : position + record_length]
        position += record_length
        if not raw_record:
            continue

        row: dict[str, str] = {}
        row["_deleted"] = "1" if raw_record[0:1] == b"*" else "0"
        field_offset = 1
        for name, length in fields:
            raw_value = raw_record[field_offset : field_offset + length]
            row[name] = raw_value.decode("latin1", errors="ignore").strip()
            field_offset += length
        records.append(row)

    return [name for name, _ in fields], records


def detect_zip_field(fields: Iterable[str], requested_field: str | None) -> str:
    fields_list = list(fields)
    if requested_field:
        if requested_field not in fields_list:
            raise ValueError(
                f"Shapefile DBF does not contain ZIP field '{requested_field}'. "
                f"Available fields: {fields_list}"
            )
        return requested_field

    lower_to_original = {field.lower(): field for field in fields_list}
    for candidate in DEFAULT_ZIP_FIELDS:
        if candidate.lower() in lower_to_original:
            return lower_to_original[candidate.lower()]

    raise ValueError(
        "Could not detect the ZIP/ZCTA field in the shapefile DBF. "
        f"Available fields: {fields_list}. Pass --shp-zip-col explicitly."
    )


def read_polygon_shapes(shp_path: Path) -> list[list[list[tuple[float, float]]]]:
    data = shp_path.read_bytes()
    if len(data) < 100:
        raise ValueError(f"{shp_path} is not a valid shapefile.")

    file_code = struct.unpack(">i", data[:4])[0]
    if file_code != 9994:
        raise ValueError(f"{shp_path} is not a valid ESRI shapefile.")

    shapes: list[list[list[tuple[float, float]]]] = []
    position = 100
    while position + 8 <= len(data):
        _, content_length_words = struct.unpack(">2i", data[position : position + 8])
        position += 8
        content_length = content_length_words * 2
        content = data[position : position + content_length]
        position += content_length

        if len(content) < 4:
            continue

        shape_type = struct.unpack("<i", content[:4])[0]
        if shape_type == 0:
            shapes.append([])
            continue
        if shape_type not in {5, 15, 25}:
            raise ValueError(
                f"Unsupported shape type {shape_type} in {shp_path}. "
                "Expected Polygon, PolygonZ, or PolygonM."
            )

        if len(content) < 44:
            shapes.append([])
            continue

        num_parts = struct.unpack("<i", content[36:40])[0]
        num_points = struct.unpack("<i", content[40:44])[0]
        parts_start = 44
        points_start = parts_start + (num_parts * 4)

        if len(content) < points_start + (num_points * 16):
            raise ValueError(f"Corrupt polygon record found in {shp_path}.")

        part_indexes = list(struct.unpack(f"<{num_parts}i", content[parts_start:points_start]))
        points_raw = content[points_start : points_start + (num_points * 16)]
        points = [
            struct.unpack("<2d", points_raw[i * 16 : (i + 1) * 16])
            for i in range(num_points)
        ]

        rings: list[list[tuple[float, float]]] = []
        for part_index, start in enumerate(part_indexes):
            end = part_indexes[part_index + 1] if part_index + 1 < num_parts else num_points
            ring = points[start:end]
            if len(ring) >= 3:
                rings.append(ring)
        shapes.append(rings)

    return shapes


def load_selected_shapes(
    shp_path: Path,
    selected_zips: set[str],
    shp_zip_col: str | None,
    allow_missing_shapes: bool,
) -> list[ShapeRecord]:
    dbf_path = shp_path.with_suffix(".dbf")
    if not shp_path.exists():
        raise FileNotFoundError(f"Shapefile not found: {shp_path}")
    if not dbf_path.exists():
        raise FileNotFoundError(f"DBF sidecar not found: {dbf_path}")

    fields, dbf_records = read_dbf_records(dbf_path)
    zip_field = detect_zip_field(fields, shp_zip_col)
    polygon_shapes = read_polygon_shapes(shp_path)

    if len(polygon_shapes) != len(dbf_records):
        raise ValueError(
            f"Shapefile geometry count ({len(polygon_shapes)}) does not match "
            f"DBF record count ({len(dbf_records)})."
        )

    selected_shapes: list[ShapeRecord] = []
    found_zips: set[str] = set()
    for attrs, rings in zip(dbf_records, polygon_shapes):
        if attrs.get("_deleted") == "1":
            continue
        zip_code = normalize_zip(attrs.get(zip_field))
        if zip_code not in selected_zips or not rings:
            continue
        selected_shapes.append(ShapeRecord(zip_code=zip_code, rings_lon_lat=rings))
        found_zips.add(zip_code)

    missing = sorted(selected_zips - found_zips)
    if missing:
        missing_preview = f"{', '.join(missing[:20])}{'...' if len(missing) > 20 else ''}"
        message = (
            f"{len(missing)} ZIPs from the CSV were not found in the shapefile: "
            f"{missing_preview}"
        )
        if not allow_missing_shapes:
            raise ValueError(
                f"{message}. The grid would not cover the full CSV-defined Houston area. "
                "Use a ZCTA shapefile containing all CSV ZIPs, or pass "
                "--allow-missing-shapes to intentionally build a partial grid."
            )
        print(f"WARNING: {message}")

    if not selected_shapes:
        raise ValueError("None of the CSV ZIP codes were found in the shapefile.")

    return selected_shapes


def project_shapes(
    shapes: list[ShapeRecord],
    projector: LocalAzimuthalEquidistant,
) -> list[ProjectedShape]:
    projected: list[ProjectedShape] = []
    for shape in shapes:
        rings_xy: list[list[tuple[float, float]]] = []
        xs: list[float] = []
        ys: list[float] = []
        for ring in shape.rings_lon_lat:
            projected_ring = [projector.forward(lat=lat, lon=lon) for lon, lat in ring]
            if len(projected_ring) >= 3:
                rings_xy.append(projected_ring)
                xs.extend(x for x, _ in projected_ring)
                ys.extend(y for _, y in projected_ring)

        if xs and ys:
            projected.append(
                ProjectedShape(
                    zip_code=shape.zip_code,
                    rings_xy=rings_xy,
                    bbox=(min(xs), min(ys), max(xs), max(ys)),
                )
            )

    if not projected:
        raise ValueError("No valid projected ZIP polygons were created.")
    return projected


def point_on_segment(
    px: float,
    py: float,
    ax: float,
    ay: float,
    bx: float,
    by: float,
    tolerance: float = 1e-9,
) -> bool:
    cross = (px - ax) * (by - ay) - (py - ay) * (bx - ax)
    if abs(cross) > tolerance:
        return False

    dot = (px - ax) * (px - bx) + (py - ay) * (py - by)
    return dot <= tolerance


def point_in_ring(px: float, py: float, ring: list[tuple[float, float]]) -> bool:
    inside = False
    previous_x, previous_y = ring[-1]

    for current_x, current_y in ring:
        if point_on_segment(px, py, previous_x, previous_y, current_x, current_y):
            return True

        crosses_y = (current_y > py) != (previous_y > py)
        if crosses_y:
            x_intersection = (
                (previous_x - current_x) * (py - current_y)
                / (previous_y - current_y)
                + current_x
            )
            if px < x_intersection:
                inside = not inside

        previous_x, previous_y = current_x, current_y

    return inside


def point_in_shape(px: float, py: float, shape: ProjectedShape) -> bool:
    min_x, min_y, max_x, max_y = shape.bbox
    if px < min_x or px > max_x or py < min_y or py > max_y:
        return False

    containing_ring_count = sum(
        1 for ring in shape.rings_xy if point_in_ring(px, py, ring)
    )
    return containing_ring_count % 2 == 1


def containing_zip(
    px: float,
    py: float,
    shapes: list[ProjectedShape],
) -> str | None:
    for shape in shapes:
        if point_in_shape(px, py, shape):
            return shape.zip_code
    return None


def generate_grid_rows(
    shapes: list[ProjectedShape],
    projector: LocalAzimuthalEquidistant,
    cell_size_miles: float,
    city: str,
    state: str,
    coordinate_precision: int,
) -> list[dict[str, object]]:
    cell_size_meters = cell_size_miles * MILE_IN_METERS
    min_x = min(shape.bbox[0] for shape in shapes)
    min_y = min(shape.bbox[1] for shape in shapes)
    max_x = max(shape.bbox[2] for shape in shapes)
    max_y = max(shape.bbox[3] for shape in shapes)

    start_x = math.floor(min_x / cell_size_meters) * cell_size_meters
    start_y = math.floor(min_y / cell_size_meters) * cell_size_meters
    column_count = math.ceil((max_x - start_x) / cell_size_meters)
    row_count = math.ceil((max_y - start_y) / cell_size_meters)

    rows: list[dict[str, object]] = []
    next_id = 1
    for row_index in range(row_count):
        center_y = start_y + ((row_index + 0.5) * cell_size_meters)
        for column_index in range(column_count):
            center_x = start_x + ((column_index + 0.5) * cell_size_meters)
            zip_code = containing_zip(center_x, center_y, shapes)
            if zip_code is None:
                continue

            latitude, longitude = projector.inverse(center_x, center_y)
            rows.append(
                {
                    "grid_id": f"HOU-GRID-{next_id:05d}",
                    "city": city,
                    "state": state,
                    "containing_zip": zip_code,
                    "row": row_index,
                    "col": column_index,
                    "latitude": round(latitude, coordinate_precision),
                    "longitude": round(longitude, coordinate_precision),
                    "cell_size_miles": cell_size_miles,
                }
            )
            next_id += 1

    return rows


def write_grid_csv(rows: list[dict[str, object]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "grid_id",
        "city",
        "state",
        "containing_zip",
        "row",
        "col",
        "latitude",
        "longitude",
        "cell_size_miles",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a 1-mile Houston grid from the ZIPs listed in houston_zips.csv "
            "and return each square centroid as latitude/longitude."
        )
    )
    parser.add_argument(
        "--zips-csv",
        default=None,
        help="CSV containing Houston ZIP labels. Defaults to houston_zips.csv if present.",
    )
    parser.add_argument(
        "--zip-column",
        default="zip",
        help="ZIP column in --zips-csv. Default: zip",
    )
    parser.add_argument(
        "--zip-shapefile",
        default=str(DEFAULT_ZCTA_SHAPEFILE),
        help="Filtered Census ZCTA .shp file containing ZIP polygons.",
    )
    parser.add_argument(
        "--shp-zip-col",
        default=None,
        help="ZIP/ZCTA field in the shapefile DBF. Auto-detected by default.",
    )
    parser.add_argument(
        "--allow-missing-shapes",
        action="store_true",
        help=(
            "Allow output even when some ZIPs in --zips-csv are missing from "
            "the shapefile. By default, missing ZIP geometry is an error."
        ),
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output CSV path. Default: static data/houston_grid_centroids.csv",
    )
    parser.add_argument(
        "--cell-size-miles",
        type=float,
        default=1.0,
        help="Grid square width/height in miles. Default: 1.0",
    )
    parser.add_argument("--city", default="Houston")
    parser.add_argument("--state", default="TX")
    parser.add_argument(
        "--origin-lat",
        type=float,
        default=None,
        help="Optional projection origin latitude. Defaults to mean CSV latitude.",
    )
    parser.add_argument(
        "--origin-lon",
        type=float,
        default=None,
        help="Optional projection origin longitude. Defaults to mean CSV longitude.",
    )
    parser.add_argument(
        "--coordinate-precision",
        type=int,
        default=7,
        help="Decimal places for latitude/longitude in output. Default: 7",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.cell_size_miles <= 0:
        raise SystemExit("--cell-size-miles must be greater than zero.")
    if (args.origin_lat is None) != (args.origin_lon is None):
        raise SystemExit("--origin-lat and --origin-lon must be supplied together.")

    zips_csv = Path(args.zips_csv) if args.zips_csv else resolve_default_zip_csv()
    if not zips_csv.exists():
        raise SystemExit(
            f"ZIP CSV not found: {zips_csv}. Pass --zips-csv with the correct path."
        )

    try:
        origin_lat, origin_lon = (
            (args.origin_lat, args.origin_lon)
            if args.origin_lat is not None and args.origin_lon is not None
            else load_projection_origin(zips_csv)
        )

        selected_zips = load_zip_codes(zips_csv, args.zip_column)
        shape_records = load_selected_shapes(
            shp_path=Path(args.zip_shapefile),
            selected_zips=selected_zips,
            shp_zip_col=args.shp_zip_col,
            allow_missing_shapes=args.allow_missing_shapes,
        )

        projector = LocalAzimuthalEquidistant(origin_lat=origin_lat, origin_lon=origin_lon)
        projected_shapes = project_shapes(shape_records, projector)
        rows = generate_grid_rows(
            shapes=projected_shapes,
            projector=projector,
            cell_size_miles=args.cell_size_miles,
            city=args.city,
            state=args.state,
            coordinate_precision=args.coordinate_precision,
        )

        output_path = Path(args.output)
        write_grid_csv(rows, output_path)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(f"ERROR: {exc}")

    print(f"Loaded {len(selected_zips)} ZIP codes from {zips_csv}.")
    print(f"Matched {len(shape_records)} ZIP polygons from {args.zip_shapefile}.")
    print(f"Wrote {len(rows)} grid centroids to {output_path}.")
    print("Selection rule: grid square centroid must fall inside the Houston ZIP union.")


if __name__ == "__main__":
    main()
