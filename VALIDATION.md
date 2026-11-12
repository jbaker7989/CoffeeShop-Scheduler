# Weather×Sales Validation Report (CS-001)

Generated: 2026-11-11 21:58:08 by `data/validate_weather_forecast.py`.
Overall: **PASS** (5/5 checks passed).

| File | Modified |
| --- | --- |
| `coffee_shop_sales.xlsx` | 2023-10-13 12:18:17 |
| `weather_forecast.xlsx` | 2026-11-07 00:43:09 |

Sheets in `weather_forecast.xlsx`: README, Hourly_Sales_Weather, Daily_Sales_Weather, Weather_Codes (the `README` sheet is expected — documents source/units/license — not a defect).

## ✅ A. Hourly join correctness — PASS
- Checked 36 rows (30 random seed=42 + 6 edge), 0 mismatches.

## ✅ B. Hour-window & zero-fill invariants — PASS
- Rows outside hours 6-22: 0 (expect 0).
- line_items==0 rows with in_trading_hours!=False or revenue!=0: 0 (expect 0).
- line_items>0 rows with in_trading_hours!=True: 0 (expect 0).

## ✅ C. Daily integrity — PASS
- Daily rows: 543 (expect 543 = 3 stores x 181 days); stores [3, 5, 8].
-   store 3: 181 dates, continuous, no gaps.
-   store 5: 181 dates, continuous, no gaps.
-   store 8: 181 dates, continuous, no gaps.
- Daily == sum(hourly sheet) for all 543 store-days.
- [info] Sales rows outside hours 6-22 (omitted from sheets): 0 rows, 0 line_items, $0.00 revenue.

## ✅ D. Weather_Codes completeness — PASS
- Distinct codes in hourly: 13; in lookup: 28; orphans: none.
- weather_desc disagreements: 0; weather_bucket disagreements: 0 (expect 0).

## ✅ E. Coverage — PASS
- Stores hourly=[3, 5, 8] daily=[3, 5, 8] (expect [3, 5, 8]).
- Date range 2023-01-01 .. 2023-06-30 (expect 2023-01-01 .. 2023-06-30).
