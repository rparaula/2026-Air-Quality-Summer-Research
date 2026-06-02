from collect import get_zip_centroids


def test_get_zip_centroids_filters_city_state_and_cleans_rows(tmp_path):
    fake_uszips = tmp_path / "uszips.csv"

    fake_uszips.write_text(
        "zip,city,state_id,lat,lng\n"
        "77002,Houston,TX,29.756,-95.365\n"
        "77003,Houston,TX,29.749,-95.345\n"
        "77003,Houston,TX,29.749,-95.345\n"
        "73301,Austin,TX,30.267,-97.743\n"
        "99999,Houston,TX,,\n"
    )

    result = get_zip_centroids(
        city="Houston",
        state_id="TX",
        uszips_csv_path=fake_uszips,
    )

    assert list(result.columns) == ["zip", "latitude", "longitude"]

    assert len(result) == 2

    assert set(result["zip"].astype(str)) == {"77002", "77003"}

    assert result["latitude"].notna().all()
    assert result["longitude"].notna().all()