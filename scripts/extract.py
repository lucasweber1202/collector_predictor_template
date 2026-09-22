"""Source-specific extraction, plus the two contracts every collector owes.

A concrete collector replaces ``collect()`` with its own fetch/parse and
implements ``parse_series_id`` / ``build_series_id`` for its own grammar. The
usable-series filter below is fleet logic and is copied unchanged.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date
from typing import Any

from scripts.config import MAX_STALE_MONTHS, MIN_HISTORY_YEARS

logger = logging.getLogger(__name__)


# -- series_id contract ---------------------------------------------------
# series_id is uppercase, underscore-separated, ordered coarse -> fine, and
# round-trippable: build_series_id(*parse_series_id(sid)) == sid. It encodes
# economic identity only -- never a delivery provider, vendor ticker or any
# other implementation detail of how the value reached us.


def parse_series_id(series_id: str) -> tuple[str, ...]:
    """Split a structured series_id into its components.

    Raises ValueError on a malformed id. Concrete collectors override this.
    """
    raise NotImplementedError("Replace scripts/extract.py with the source-specific collector")


def build_series_id(*components: str) -> str:
    """Rebuild a series_id from the tuple parse_series_id returned."""
    raise NotImplementedError("Replace scripts/extract.py with the source-specific collector")


# -- 5.1 usable-series filtering ------------------------------------------


@dataclass(frozen=True)
class UsabilityReport:
    """What the filter removed, for logging and for tests to assert on."""

    kept: tuple[str, ...]
    stale: tuple[str, ...]
    short_history: tuple[str, ...]
    empty: tuple[str, ...]

    @property
    def dropped(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.stale) | set(self.short_history) | set(self.empty)))


def _months_between(earlier: date, later: date) -> int:
    """Whole months from ``earlier`` to ``later``, day-of-month aware."""
    months = (later.year - earlier.year) * 12 + (later.month - earlier.month)
    if later.day < earlier.day:
        months -= 1
    return months


def _is_valid(value: Any) -> bool:
    """A real observation: present, numeric and finite."""
    if value is None:
        return False
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(numeric)


def assess_series(
    reference_dates: list[date],
    today: date,
    max_stale_months: int = MAX_STALE_MONTHS,
    min_history_years: float = MIN_HISTORY_YEARS,
) -> str:
    """Classify one series from the reference dates of its valid observations.

    Returns ``"keep"``, ``"empty"``, ``"stale"`` or ``"short_history"``.
    Recency is judged at the period end and over non-null values only: a source
    that keeps listing a discontinued series with empty recent cells must not
    look live because of those blanks.
    """
    if not reference_dates:
        return "empty"
    first, last = min(reference_dates), max(reference_dates)
    if _months_between(last, today) > max_stale_months:
        return "stale"
    if _months_between(first, last) < round(min_history_years * 12):
        return "short_history"
    return "keep"


def filter_usable_series(
    observations: list[Any],
    catalog: dict[str, dict[str, Any]],
    today: date,
    max_stale_months: int = MAX_STALE_MONTHS,
    min_history_years: float = MIN_HISTORY_YEARS,
) -> tuple[list[Any], dict[str, dict[str, Any]], UsabilityReport]:
    """Drop obsolete and history-less series before anything is persisted.

    Runs after parsing and before the time_series / metadata upsert, so the
    standardized tables never carry a dead or stub series, and prunes the
    catalog alongside the observations so metadata can never describe a series
    the database does not hold (GUIDELINES.md 5.1).
    """
    valid_dates: dict[str, list[date]] = {}
    for observation in observations:
        if _is_valid(observation.value):
            valid_dates.setdefault(observation.series_id, []).append(observation.reference_date)

    verdicts: dict[str, str] = {}
    for series_id in set(catalog) | {o.series_id for o in observations}:
        verdicts[series_id] = assess_series(
            valid_dates.get(series_id, []), today, max_stale_months, min_history_years
        )

    keep = {series_id for series_id, verdict in verdicts.items() if verdict == "keep"}
    report = UsabilityReport(
        kept=tuple(sorted(keep)),
        stale=tuple(sorted(s for s, v in verdicts.items() if v == "stale")),
        short_history=tuple(sorted(s for s, v in verdicts.items() if v == "short_history")),
        empty=tuple(sorted(s for s, v in verdicts.items() if v == "empty")),
    )

    if report.dropped:
        logger.info(
            "Usable-series filter: kept %d, dropped %d "
            "(stale=%d short_history=%d empty=%d; max_stale_months=%d min_history_years=%s)",
            len(report.kept),
            len(report.dropped),
            len(report.stale),
            len(report.short_history),
            len(report.empty),
            max_stale_months,
            min_history_years,
        )
        for series_id in report.stale:
            logger.info(
                "Dropped %s: last valid observation older than %d months",
                series_id,
                max_stale_months,
            )
        for series_id in report.short_history:
            logger.info(
                "Dropped %s: valid history shorter than %s years", series_id, min_history_years
            )
        for series_id in report.empty:
            logger.info("Dropped %s: no valid observations", series_id)
    else:
        logger.info("Usable-series filter: all %d series usable", len(report.kept))

    kept_observations = [o for o in observations if o.series_id in keep]
    kept_catalog = {sid: fields for sid, fields in catalog.items() if sid in keep}
    return kept_observations, kept_catalog, report


def collect(start_date: date) -> Any:
    """Fetch and parse every observation on or after ``start_date``."""
    raise NotImplementedError("Replace scripts/extract.py with the source-specific collector")
