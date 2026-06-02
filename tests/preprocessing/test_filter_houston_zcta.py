import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from preprocessing.filter_houston_zcta import normalize_zip, filter_shapefile_by_dataset_zips


def make_test_shapefile(path, zip_column="ZCTA5CE20"):
    gdf = gpd.GeoDataFrame(
        {
            zip_column: ["77002", "77003", "77004"],
            "name": ["A", "B", "C"],
            "geometry": [
                Point(-95.36, 29.76),
                Point(-95.37, 29.77),
                Point(-95.38, 29.78),
            ],
        },
        crs="EPSG:4326",
    )

    gdf.to_file(path)
    return path


def test_normalize_zip():
    assert normalize_zip(77002) == "77002"
    assert normalize_zip("77002") == "77002"
    assert normalize_zip(7702) == "07702"
    assert normalize_zip(" 77002 ") == "77002"
    assert normalize_zip(77002.0) == "77002"
    assert normalize_zip(None) is None


def test_filter_shapefile_by_dataset_zips_keeps_only_dataset_zips(tmp_path, capsys):
    csv_path = tmp_path / "air_quality.csv"
    input_shp_path = tmp_path / "input_zcta.shp"
    output_shp_path = tmp_path / "filtered_zcta.shp"

    # Current implementation expects CSV input.
    # When the preprocessing pipeline moves to Parquet, change this fixture to
    # write a Parquet file and update the production function to use read_parquet.
    pd.DataFrame(
        {
            "zip": ["77002", "77003", "99999", "77002", None],
            "pm25": [10.1, 12.5, 8.2, 11.0, 9.9],
        }
    ).to_csv(csv_path, index=False)

    make_test_shapefile(input_shp_path)

    filter_shapefile_by_dataset_zips(
        csv_path=csv_path,
        shp_path=input_shp_path,
        output_path=output_shp_path,
    )

    output_gdf = gpd.read_file(output_shp_path)

    assert set(output_gdf["ZCTA5CE20"]) == {"77002", "77003"}
    assert len(output_gdf) == 2
    assert "_zip_norm" not in output_gdf.columns

    captured = capsys.readouterr()

    assert "Found 3 unique ZIP codes in dataset." in captured.out
    assert "Original shapefile rows: 3" in captured.out
    assert "Filtered shapefile rows: 2" in captured.out
    assert "Warning: 1 dataset ZIP codes were not found in the shapefile." in captured.out
    assert "99999" in captured.out


def test_filter_shapefile_raises_error_when_csv_zip_column_missing(tmp_path):
    csv_path = tmp_path / "air_quality.csv"
    input_shp_path = tmp_path / "input_zcta.shp"
    output_shp_path = tmp_path / "filtered_zcta.shp"

    pd.DataFrame(
        {
            "postal_code": ["77002", "77003"],
            "pm25": [10.1, 12.5],
        }
    ).to_csv(csv_path, index=False)

    make_test_shapefile(input_shp_path)

    with pytest.raises(ValueError, match="CSV file does not contain column 'zip'"):
        filter_shapefile_by_dataset_zips(
            csv_path=csv_path,
            shp_path=input_shp_path,
            output_path=output_shp_path,
        )


def test_filter_shapefile_raises_error_when_shapefile_zip_column_missing(tmp_path):
    csv_path = tmp_path / "air_quality.csv"
    input_shp_path = tmp_path / "input_zcta.shp"
    output_shp_path = tmp_path / "filtered_zcta.shp"

    pd.DataFrame(
        {
            "zip": ["77002", "77003"],
            "pm25": [10.1, 12.5],
        }
    ).to_csv(csv_path, index=False)

    make_test_shapefile(input_shp_path, zip_column="bad_zip")

    with pytest.raises(ValueError, match="Shapefile does not contain column 'ZCTA5CE20'"):
        filter_shapefile_by_dataset_zips(
            csv_path=csv_path,
            shp_path=input_shp_path,
            output_path=output_shp_path,
        )



def test_filter_shapefile_supports_custom_zip_columns(tmp_path):
    csv_path = tmp_path / "air_quality.csv"
    input_shp_path = tmp_path / "input_zcta.shp"
    output_shp_path = tmp_path / "filtered_zcta.shp"

    # Current implementation expects CSV input.
    # When moving to Parquet, replace this with to_parquet/read_parquet behavior.
    pd.DataFrame(
        {
            "postal_code": ["77002", "77003"],
            "pm25": [10.1, 12.5],
        }
    ).to_csv(csv_path, index=False)

    gdf = gpd.GeoDataFrame(
        {
            "GEOID20": ["77002", "77003", "77004"],
            "geometry": [
                Point(-95.36, 29.76),
                Point(-95.37, 29.77),
                Point(-95.38, 29.78),
            ],
        },
        crs="EPSG:4326",
    )
    gdf.to_file(input_shp_path)

    filter_shapefile_by_dataset_zips(
        csv_path=csv_path,
        shp_path=input_shp_path,
        output_path=output_shp_path,
        csv_zip_col="postal_code",
        shp_zip_col="GEOID20",
    )

    output_gdf = gpd.read_file(output_shp_path)

    assert set(output_gdf["GEOID20"]) == {"77002", "77003"}
    assert len(output_gdf) == 2
    
    


def test_filter_shapefile_with_only_null_dataset_zips_writes_empty_output(tmp_path):
    csv_path = tmp_path / "air_quality.csv"
    input_shp_path = tmp_path / "input_zcta.shp"
    output_shp_path = tmp_path / "filtered_zcta.shp"

    # Current implementation expects CSV input.
    # When moving to Parquet, replace this with to_parquet/read_parquet behavior.
    pd.DataFrame(
        {
            "zip": [None, None],
            "pm25": [10.1, 12.5],
        }
    ).to_csv(csv_path, index=False)

    make_test_shapefile(input_shp_path)

    filter_shapefile_by_dataset_zips(
        csv_path=csv_path,
        shp_path=input_shp_path,
        output_path=output_shp_path,
    )

    output_gdf = gpd.read_file(output_shp_path)

    assert output_gdf.empty
    assert output_shp_path.exists()
    assert "_zip_norm" not in output_gdf.columns
    
    



@pytest.mark.parametrize(
    "raw_value, expected",
    [
        (pd.NA, None),
        (float("nan"), None),
        ("77002.0", "77002"),
        ("7702", "07702"),
        ("abc", "00abc"),
    ],
)
def test_normalize_zip_edge_cases(raw_value, expected):
    assert normalize_zip(raw_value) == expected