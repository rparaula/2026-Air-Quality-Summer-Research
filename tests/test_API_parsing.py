import pandas as pd
import requests_cache
from collect import response_to_dataframe


class FakeVariable:
    def __init__(self, values):
        self.values = values

    def ValuesAsNumpy(self):
        return self.values


class FakeHourly:
    def Time(self):
        return 1775001600  # 2026-04-01 00:00:00 UTC

    def TimeEnd(self):
        return 1775008800  # 2026-04-01 02:00:00 UTC

    def Interval(self):
        return 3600  # hourly

    def Variables(self, index):
        fake_values = [
            [42, 43],      # us_aqi
            [8.5, 8.8],    # pm2_5
            [70, 72],      # ozone
        ]
        return FakeVariable(fake_values[index])


class FakeResponse:
    def Hourly(self):
        return FakeHourly()


def test_response_to_dataframe_parses_fake_air_quality_response():
    location_row = pd.Series({
        "city": "Houston",
        "state": "TX",
        "zip": "77002",
        "latitude": 29.756,
        "longitude": -95.365,
    })

    hourly_vars = ["us_aqi", "pm2_5", "ozone"]

    result = response_to_dataframe(
        resp=FakeResponse(),
        location_row=location_row,
        hourly_vars=hourly_vars,
        timezone="America/Chicago",
    )

    assert len(result) == 2

    assert list(result.columns) == [
        "city",
        "state",
        "zip",
        "latitude",
        "longitude",
        "time",
        "us_aqi",
        "pm2_5",
        "ozone",
    ]

    assert result.loc[0, "city"] == "Houston"
    assert result.loc[0, "state"] == "TX"
    assert result.loc[0, "zip"] == "77002"
    assert result.loc[0, "us_aqi"] == 42
    assert result.loc[1, "us_aqi"] == 43
    assert result.loc[0, "pm2_5"] == 8.5
    assert result.loc[1, "ozone"] == 72

    assert pd.api.types.is_datetime64_any_dtype(result["time"])