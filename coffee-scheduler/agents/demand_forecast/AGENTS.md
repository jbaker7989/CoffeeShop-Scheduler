# Demand-forecast agent

## Owns

- Build versioned forecast features, train/select supported models, and produce forecasts through the
  interfaces in `agents/demand_forecast/`.
- Validate results against `contracts/forecast.py` and the pinned product taxonomy before publication.
- Report model/version provenance, uncertainty, missing inputs, and evaluation evidence honestly.

## Does not own

- Do not create schedule assignments, declare legal staffing, or replace the scheduler/compliance outputs.
- Do not turn absent observations into observed zeroes unless the source contract explicitly defines that
  zero-fill semantics; preserve masks and missingness.
- Do not promote a model or claim accuracy outside the held-out acceptance gate and documented data window.
- Do not substitute synthetic fixtures for missing customer or production data.

## Inputs

- Tenant-scoped source sales, taxonomy, operating hours, calendar and weather records through supported
  data adapters.
- Training/inference rows with explicit feature version, timezone, target availability and missingness.
- The versioned output shape in [`contracts/forecast.py`](../../contracts/forecast.py).

## Outputs

- Contract-valid predictions with location, category, timezone-aware forecast hour, quantile/interval,
  taxonomy/model/contract versions, and provenance.
- Accuracy, coverage and data-quality diagnostics for the named evaluation period; missing observations
  remain visible.

## Skills

- For source-join validation, units and missingness, use
  [`skills/sales-weather-join/SKILL.md`](../../skills/sales-weather-join/SKILL.md).
- For changes to calibration inputs or thresholds, use
  [`skills/standards-calibration/SKILL.md`](../../skills/standards-calibration/SKILL.md).
- Follow [`../../test/SKILL.md`](../../test/SKILL.md) and [`../../AGENTS.md`](../../AGENTS.md).
