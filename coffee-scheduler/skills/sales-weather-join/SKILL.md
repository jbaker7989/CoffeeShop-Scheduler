# Skill: sales-weather-join

Join historical sales with weather data for demand signal extraction.

## Procedure

1. Read source metadata and attribution first (`ATTRIBUTION.md`, data README, and provider/source files).
   Preserve license and permitted-use limits with every derived artifact.
2. Validate each source's declared grain, timezone, units, timestamp semantics, null conventions, and key
   uniqueness before joining. Treat numeric-looking identifiers as strings when the schema says text.
3. Normalize timestamps into the explicitly chosen local/business timezone and join only on the documented
   store/date/hour keys. Do not infer a timezone from the machine or silently round sub-hour timestamps.
4. Measure unmatched rows and duplicate-key counts before aggregation. Keep missing weather distinct from a
   measured zero and do not fill closed hours as observed demand.
5. Write a deterministic derived dataset with source window, source versions, join keys and row-count
   diagnostics; validate sampled joined values against the source files independently.
6. Report correlation/association as exploratory unless the sample and design support a stronger claim.
   Holdout evaluation decides whether a signal can influence forecasts or calibrated parameters.

## Validation

- Assert expected key uniqueness and report source rows, matched rows, unmatched rows and duplicates.
- Check random joined rows back against both input sources and verify units/timezone conversions.
- Exercise absent hours, closed hours, DST transitions, duplicate keys, missing weather, unexpected stores,
  and renamed/unknown categories.
- Re-run on the same inputs and verify stable output; preserve attribution and data-use restrictions.

## Failure modes

- Missing/ambiguous timezone, unit, or key definition: stop before the join; do not guess.
- Duplicate or unmatched keys beyond the documented policy: fail with examples and counts rather than
  selecting an arbitrary row.
- Provider absence or missing observations: retain null/missingness indicators, never fabricate an observed
  zero.
- Data-use terms prohibit intended use: do not publish or use the derived data for that purpose.
- A relationship worsens a held-out forecast or is season-confounded: document the result and leave the
  production multiplier unchanged.
