import json
from datetime import date
import state


def test_load_state_when_file_missing(tmp_path, monkeypatch):
    fake_state_file = tmp_path / "state.json"
    monkeypatch.setattr(state, "STATE_FILE", fake_state_file)

    result = state.load_state()

    assert result == {"last_ingested_date": None}


def test_load_state_when_file_has_invalid_json(tmp_path, monkeypatch):
    fake_state_file = tmp_path / "state.json"
    fake_state_file.write_text("{bad json")

    monkeypatch.setattr(state, "STATE_FILE", fake_state_file)

    result = state.load_state()

    assert result == {"last_ingested_date": None}


def test_save_state_writes_last_ingested_date(tmp_path, monkeypatch):
    fake_state_file = tmp_path / "state.json"
    monkeypatch.setattr(state, "STATE_FILE", fake_state_file)

    state.save_state("2026-04-01")

    saved = json.loads(fake_state_file.read_text())

    assert saved["last_ingested_date"] == "2026-04-01"
    assert "last_run_timestamp" in saved


def test_mark_in_progress_adds_lock(tmp_path, monkeypatch):
    fake_state_file = tmp_path / "state.json"
    monkeypatch.setattr(state, "STATE_FILE", fake_state_file)

    state.mark_in_progress("2026-04-01", "2026-04-01")

    saved = json.loads(fake_state_file.read_text())

    assert saved["in_progress"]["start"] == "2026-04-01"
    assert saved["in_progress"]["end"] == "2026-04-01"
    assert "started_at" in saved["in_progress"]


def test_clear_in_progress_removes_lock(tmp_path, monkeypatch):
    fake_state_file = tmp_path / "state.json"
    monkeypatch.setattr(state, "STATE_FILE", fake_state_file)

    state.mark_in_progress("2026-04-01", "2026-04-01")
    state.clear_in_progress()

    saved = json.loads(fake_state_file.read_text())

    assert "in_progress" not in saved
    
    

class FakeDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 4, 10)


def test_compute_window_when_no_previous_state(tmp_path, monkeypatch):
    fake_state_file = tmp_path / "state.json"

    monkeypatch.setattr(state, "STATE_FILE", fake_state_file)
    monkeypatch.setattr(state, "date", FakeDate)

    start, end = state.compute_window()

    assert start == "2026-04-05"
    assert end == "2026-04-05"


def test_compute_window_when_previous_state_exists(tmp_path, monkeypatch):
    fake_state_file = tmp_path / "state.json"
    fake_state_file.write_text(json.dumps({"last_ingested_date": "2026-04-03"}))

    monkeypatch.setattr(state, "STATE_FILE", fake_state_file)
    monkeypatch.setattr(state, "date", FakeDate)

    start, end = state.compute_window()

    assert start == "2026-04-04"
    assert end == "2026-04-05"


def test_compute_window_when_already_up_to_date(tmp_path, monkeypatch):
    fake_state_file = tmp_path / "state.json"
    fake_state_file.write_text(json.dumps({"last_ingested_date": "2026-04-05"}))

    monkeypatch.setattr(state, "STATE_FILE", fake_state_file)
    monkeypatch.setattr(state, "date", FakeDate)

    start, end = state.compute_window()

    assert start is None
    assert end is None