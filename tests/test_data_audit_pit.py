"""Regression tests for current-file backfill and legacy PIT leakage."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy.engine import Engine

from main import _availability_rows
from scripts.availability import get_series_as_of, upsert_availability
from scripts.time_series import Observation, upsert_time_series

SERIES = "AUDIT_SERIES"


def test_current_mutable_file_backfill_is_first_seen() -> None:
    collected = datetime(2026, 9, 18, 12, tzinfo=UTC)
    result = SimpleNamespace(
        written_keys=[(SERIES, date(2020, 1, 1), collected.date())],
        revised_keys=set(),
        preexisting_series=set(),
    )
    data = SimpleNamespace(
        observations=[SimpleNamespace(series_id=SERIES, reference_date=date(2020, 1, 1), snapshot_id="s")],
        releases=[datetime(2020, 2, 1, 9, tzinfo=UTC)],
        min_lag_days=0,
        max_lag_days=60,
        inferred_lag_days=30,
    )

    row = _availability_rows(data, result, collected)[0]
    assert row["available_at"] == collected
    assert row["availability_basis"] == "first_seen"
    assert row["release_date"] is None


def test_legacy_official_timestamp_cannot_predate_collection(engine: Engine) -> None:
    reference = date(2020, 1, 1)
    collected = datetime(2026, 9, 18, 12, tzinfo=UTC)
    alleged_release = datetime(2020, 2, 1, 9, tzinfo=UTC)
    with engine.begin() as conn:
        written = upsert_time_series(
            conn, [Observation(SERIES, reference, 100.0, "s")], collected
        )
        upsert_availability(
            conn,
            [
                {
                    "series_id": series_id,
                    "reference_date": ref,
                    "vintage_date": vintage,
                    "release_date": alleged_release.date(),
                    "available_at": alleged_release,
                    "availability_basis": "official_timestamp",
                    "source_snapshot_id": "s",
                }
                for series_id, ref, vintage in written.written_keys
            ],
            collected,
        )

    assert get_series_as_of(engine, SERIES, alleged_release + timedelta(days=1)) == []
    assert get_series_as_of(engine, SERIES, collected)
