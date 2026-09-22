"""Regressions for the GUIDELINES.md contracts fixed in the strict round."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

import main
from scripts.config import METADATA_TABLE, SCHEMA_NAME
from scripts.extract import (
    assess_series,
    build_series_id,
    filter_usable_series,
    parse_series_id,
)
from scripts.legacy_schema import (
    CANONICAL_METADATA_COLUMNS,
    detect_legacy_metadata_columns,
    migrate_legacy_metadata,
)
from scripts.time_series import Observation, upsert_time_series

TODAY = date(2026, 3, 2)


def _series(
    series_id: str, months: list[int], year: int = 2025, value: float = 1.0
) -> list[Observation]:
    return [Observation(series_id, date(year, m, 1), value, "snap") for m in months]


# -- 5.1 usable-series filtering ------------------------------------------


def test_live_long_history_series_is_retained() -> None:
    observations = [
        Observation("LIVE", date(y, m, 1), 1.0, "snap") for y in range(2018, 2026) for m in (1, 7)
    ] + [Observation("LIVE", date(2026, 1, 1), 1.0, "snap")]
    catalog: dict[str, dict[str, object]] = {"LIVE": {}}
    kept, kept_catalog, report = filter_usable_series(observations, catalog, TODAY)
    assert report.kept == ("LIVE",)
    assert report.dropped == ()
    assert kept_catalog == {"LIVE": {}}
    assert len(kept) == len(observations)


def test_stale_series_is_removed_with_its_catalog_entry() -> None:
    observations = [Observation("STALE", date(y, 1, 1), 1.0, "snap") for y in range(2010, 2021)]
    kept, kept_catalog, report = filter_usable_series(observations, {"STALE": {}}, TODAY)
    assert report.stale == ("STALE",)
    assert kept == []
    assert kept_catalog == {}, "metadata must never describe a dropped series"


def test_single_observation_series_is_removed() -> None:
    _, _, report = filter_usable_series(_series("ONE", [1], year=2026), {"ONE": {}}, TODAY)
    assert report.short_history == ("ONE",)


def test_short_history_series_is_removed() -> None:
    observations = [Observation("SHORT", date(2025, m, 1), 1.0, "snap") for m in range(1, 13)]
    _, _, report = filter_usable_series(observations, {"SHORT": {}}, TODAY)
    assert report.short_history == ("SHORT",)


class _Gap:
    """A parsed row whose value the source left blank, before 5.1 prunes it."""

    def __init__(self, series_id: str, reference_date: date) -> None:
        self.series_id = series_id
        self.reference_date = reference_date
        self.value = None


def test_all_null_series_is_removed() -> None:
    observations = [_Gap("NULLS", date(2025, m, 1)) for m in range(1, 13)]
    _, _, report = filter_usable_series(observations, {"NULLS": {}}, TODAY)
    assert report.empty == ("NULLS",)


def test_trailing_nulls_do_not_make_a_dead_series_look_live() -> None:
    """A discontinued series padded with empty recent cells is still stale."""
    observations = [
        Observation("PADDED", date(y, 1, 1), 1.0, "snap") for y in range(2010, 2021)
    ] + [_Gap("PADDED", date(2026, m, 1)) for m in (1, 2)]
    _, _, report = filter_usable_series(observations, {"PADDED": {}}, TODAY)
    assert report.stale == ("PADDED",)


def test_recent_rebased_series_survives_when_threshold_allows() -> None:
    """A legitimate current-base series starting at its rebase is not a stub."""
    observations = [
        Observation("REBASED", date(y, m, 1), 1.0, "snap")
        for y in range(2021, 2026)
        for m in (1, 7)
    ] + [Observation("REBASED", date(2026, 1, 1), 1.0, "snap")]
    _, _, report = filter_usable_series(observations, {"REBASED": {}}, TODAY, min_history_years=3)
    assert report.kept == ("REBASED",)


def test_nan_and_inf_do_not_count_as_observations() -> None:
    observations = [
        Observation("BAD", date(2025, 1, 1), float("nan"), "snap"),
        Observation("BAD", date(2025, 2, 1), float("inf"), "snap"),
    ]
    _, _, report = filter_usable_series(observations, {"BAD": {}}, TODAY)
    assert report.empty == ("BAD",)


def test_assess_series_judges_recency_at_period_end() -> None:
    assert assess_series([date(2020, 1, 1), date(2026, 1, 1)], TODAY) == "keep"
    assert assess_series([date(2010, 1, 1), date(2020, 1, 1)], TODAY) == "stale"
    assert assess_series([], TODAY) == "empty"


def test_filter_logs_what_it_dropped(caplog: pytest.LogCaptureFixture) -> None:
    observations = [Observation("STALE", date(y, 1, 1), 1.0, "snap") for y in range(2010, 2021)]
    with caplog.at_level(logging.INFO, logger="scripts.extract"):
        filter_usable_series(observations, {"STALE": {}}, TODAY)
    assert "Usable-series filter" in caplog.text
    assert "dropped 1" in caplog.text


# -- series_id contract ---------------------------------------------------


def test_series_id_contract_is_exposed_from_extract() -> None:
    """The canonical public surface must exist even in the template."""
    for fn in (parse_series_id, build_series_id):
        with pytest.raises(NotImplementedError):
            fn("ANY")


# -- CLI + rewind ---------------------------------------------------------


def test_cli_accepts_start_date_and_log_level() -> None:
    args = main._parse_args(["--start-date", "2024-01-01", "--log-level", "DEBUG"])
    assert args.start_date == date(2024, 1, 1)
    assert args.log_level == "DEBUG"


def test_start_date_defaults_to_none() -> None:
    assert main._parse_args([]).start_date is None


def test_explicit_start_date_wins(engine: Engine) -> None:
    assert main.resolve_start_date(engine, date(2001, 5, 4)) == date(2001, 5, 4)


def test_fresh_database_falls_back_to_default_start_date(engine: Engine) -> None:
    from scripts.config import DEFAULT_START_DATE

    assert main.resolve_start_date(engine, None) == DEFAULT_START_DATE


def test_populated_database_rewinds_lookback_months(engine: Engine) -> None:
    from scripts.config import START_DATE_LOOKBACK_MONTHS

    with engine.begin() as conn:
        upsert_time_series(
            conn,
            [Observation("S", date(2026, 1, 15), 1.0, "snap")],
            datetime(2026, 2, 1, tzinfo=UTC),
        )
    resolved = main.resolve_start_date(engine, None)
    assert resolved == main._rewind(date(2026, 1, 15), START_DATE_LOOKBACK_MONTHS)
    assert resolved < date(2026, 1, 15)


def test_rewind_clamps_short_months() -> None:
    assert main._rewind(date(2026, 3, 31), 1) == date(2026, 2, 28)


# -- legacy schema --------------------------------------------------------


def test_canonical_schema_reports_no_drift(engine: Engine) -> None:
    with engine.begin() as conn:
        assert detect_legacy_metadata_columns(conn) == []
        assert migrate_legacy_metadata(conn) == []


def test_legacy_column_is_detected_and_migrated(engine: Engine) -> None:
    """source_id survives in source_snapshots, so dropping it loses nothing."""
    with engine.begin() as conn:
        conn.execute(
            text(f"ALTER TABLE {SCHEMA_NAME}.{METADATA_TABLE} ADD COLUMN source_id VARCHAR(100)")
        )
    with engine.begin() as conn:
        assert detect_legacy_metadata_columns(conn) == ["source_id"]
    with engine.begin() as conn:
        assert migrate_legacy_metadata(conn) == ["source_id"]
    with engine.begin() as conn:
        assert detect_legacy_metadata_columns(conn) == []
        remaining = {
            column["name"]
            for column in __import__("sqlalchemy")
            .inspect(conn)
            .get_columns(METADATA_TABLE, schema=SCHEMA_NAME)
        }
    assert remaining == set(CANONICAL_METADATA_COLUMNS)


def test_unarchivable_legacy_column_refuses_to_drop(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                f"ALTER TABLE {SCHEMA_NAME}.{METADATA_TABLE} "
                "ADD COLUMN vendor_series_id VARCHAR(200)"
            )
        )
    with pytest.raises(RuntimeError, match="would lose data"), engine.begin() as conn:
        migrate_legacy_metadata(conn)


# -- batch progress logging ----------------------------------------------


def test_insert_batches_are_logged(engine: Engine, caplog: pytest.LogCaptureFixture) -> None:
    observations = [Observation(f"S{i}", date(2026, 1, 1), float(i), "snap") for i in range(3)]
    with caplog.at_level(logging.INFO, logger="scripts.time_series"), engine.begin() as conn:
        upsert_time_series(conn, observations, datetime(2026, 3, 2, tzinfo=UTC))
    assert "Inserting 3 observation rows in 1 batches" in caplog.text
    assert "Inserted batch 1/1 (3/3 rows)" in caplog.text


def test_same_day_update_batches_are_logged(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    day = datetime(2026, 3, 2, 9, tzinfo=UTC)
    with engine.begin() as conn:
        upsert_time_series(conn, [Observation("S", date(2026, 1, 1), 1.0, "snap")], day)
    with caplog.at_level(logging.INFO, logger="scripts.time_series"), engine.begin() as conn:
        upsert_time_series(
            conn,
            [Observation("S", date(2026, 1, 1), 2.0, "snap")],
            day.replace(hour=17),
        )
    assert "Updating 1 same-day observation rows" in caplog.text
    assert "Updated batch 1/1 (1/1 rows)" in caplog.text
