# Attribution & Data Licensing

## Weather data — Open-Meteo

The weather data in `data/weather_forecast.xlsx` (and the pipeline `data/build_weather_forecast.py`)
comes from the **Open-Meteo Historical Weather API**.

- **Source:** Open-Meteo Historical Weather API (ERA5 reanalysis).
- **Endpoint used:** `https://archive-api.open-meteo.com/v1/archive`
- **Website:** https://open-meteo.com/
- **License:** data is published under **Creative Commons Attribution 4.0 International (CC BY 4.0)** —
  attribution is required wherever the data or derived values are shown or distributed.
- **Attribution statement:** "Weather data by Open-Meteo.com, licensed under CC BY 4.0."

### ⚠️ Non-commercial use only (current terms)

The **free** Open-Meteo API tier is for **non-commercial use only**. This project currently relies on
that free tier. Before any commercial deployment or demo that generates revenue, switch to a
**paid Open-Meteo API plan** (or an alternative licensed weather source). Do not ship this data
commercially under the free tier.

### Note on naming

The file is named `weather_forecast.xlsx`, but it contains **historical reanalysis** (ERA5), not a
forecast. The name is a misnomer kept for continuity; the data is backward-looking weather joined to
2023 sales, used to train the Demand Forecast agent and calibrate demand multipliers.

### Related

- Known-limitations note on the non-commercial restriction: see `FEATURES.md` → "Known limitations".
- Join validation: see `VALIDATION.md` (produced by `data/validate_weather_forecast.py`).
