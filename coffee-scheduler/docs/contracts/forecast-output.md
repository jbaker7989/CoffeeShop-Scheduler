# Forecast output contract

`forecast-output-v1` is the canonical interchange contract for F8 demand forecasts. A forecast quantity is **items (quantity) sold per `product_category`, per store, per clock-hour**. Timestamps use the `America/New_York` business timezone and are stored in PostgreSQL as `timestamptz`.

## Envelope and record fields

| Field | Type | Meaning |
| --- | --- | --- |
| `contract_version` | string | Shape and behavioral version of this envelope; currently `forecast-output-v1`. |
| `taxonomy_version` | string | F29 product vocabulary version used by every record. |
| `model_version` | string | Identifies the producing demand model and its release. |
| `generated_at` | ISO-8601 datetime | Time this envelope was generated; must carry an offset. |
| `records` | array | Forecast records. |
| `location_id` | integer | Store identifier. |
| `product_category` | string | Category from the pinned taxonomy vocabulary. |
| `forecast_hour` | ISO-8601 datetime | A timezone-aware exact clock hour. |
| `p70` | decimal string | P70 demand estimate in items. |
| `lower` | decimal string | Lower bound in items. |
| `upper` | decimal string | Upper bound in items. |
| record `model_version` | string | Must equal envelope `model_version`. |
| record `taxonomy_version` | string | Must equal envelope `taxonomy_version`. |

Each interval must satisfy `0 <= lower <= p70 <= upper`. Records are unique by `(location_id, product_category, forecast_hour)`. Store open windows are half-open: an hour at opening is included and an hour at closing is excluded.

Closed hours are omitted; they are never emitted with null forecast values. Holidays are forecast normally unless the store is closed for that holiday, in which case the closed hours are omitted.

## Versioning

`contract_version` describes the envelope and record shape. Bump it for incompatible contract shape or semantics changes. `taxonomy_version` identifies the F29 category vocabulary; bump it whenever the category taxonomy changes. `model_version` identifies the model release; bump it when the producing model, its parameters, or training/release artifact changes.

## Database mapping

Each record maps to `forecasts.tenant_id`, `location_id`, `product_category`, `forecast_hour`, `p70`, `lower`, `upper`, `model_version`, and `taxonomy_version`. Migration `0017_cs012_forecast_contract` makes the eight record columns non-null, adds the interval check, adds tenant-scoped foreign keys for taxonomy version and product category, and creates the unique live-row index on `(tenant_id, location_id, product_category, forecast_hour, model_version)`.

## JSON envelope example

```json
{
  "contract_version": "forecast-output-v1",
  "taxonomy_version": "TAX-2023H1-v1",
  "model_version": "demand-forecast-2026-01",
  "generated_at": "2026-01-05T08:00:00+00:00",
  "records": [
    {
      "location_id": 5,
      "product_category": "Coffee",
      "forecast_hour": "2026-01-05T09:00:00-05:00",
      "p70": "42.37",
      "lower": "35.00",
      "upper": "50.00",
      "model_version": "demand-forecast-2026-01",
      "taxonomy_version": "TAX-2023H1-v1"
    }
  ]
}
```

## Consumer notes

CS-016a consumes these records as solver inputs and should use the exact store/category/hour keys, including the interval values when its planning logic needs uncertainty. It must not invent forecasts for closed hours. CS-017 resolves category names through the envelope's `taxonomy_version`, not through an assumed current vocabulary.
