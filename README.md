# collector_predictor_template

Canonical starting point for standalone UK inflation predictor collectors. This
repository is a template, not an operational collector, and intentionally has
no publisher-specific endpoint, series list or parser.

## Create a collector

1. Copy this repository into `collector_<publisher>_uk`.
2. Set `SCHEMA_NAME` to the exact repository name.
3. Define the publisher, dataset, source URLs and series identifiers.
4. Implement source-specific extraction and validation in `scripts/extract.py`
   (or a flat helper only when the source genuinely forces one).
5. Document point-in-time evidence and source limitations.
6. Run `pytest`, `ruff`, `mypy`, a fresh build, an unchanged rerun, a revision
   simulation and official-source spot checks.
7. Register the collector and its raw series in `uk_inflation_predictors`.

The template supports public CSV/API/XLSX/ODS sources and daily, weekly,
monthly, quarterly or irregular administrative events. It stores five tables:
`metadata`, `time_series`, `availability`, `source_snapshots` and `logs`.

Collectors remain deliberately standalone: copy the infrastructure; never
import this repository as a Python package and never create a cross-repository
shared core.
