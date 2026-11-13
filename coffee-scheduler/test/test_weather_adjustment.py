"""BUG-004 regression specification for non-compounding weather effects."""
import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "data" / "calibrate_standards.py"


def load_calibrator():
    spec = importlib.util.spec_from_file_location("calibrate_standards", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WeatherAdjustmentSpecifications(unittest.TestCase):
    # spec: F5.ac4 — BUG-004: overlapping conditions choose one dominant effect, never a product.
    def test_overlapping_conditions_do_not_compound_multipliers(self):
        module = load_calibrator()
        row = type("Weather", (), {"precip_in": 0.2, "rain_hours": 1, "temp_min_f": 20,
                                    "temp_max_f": 40, "snow_in": 0})()
        multipliers = {"ADJ-RAIN": {"multiplier": 0.9}, "ADJ-COLD": {"multiplier": 0.7},
                       "ADJ-HEAT": {"multiplier": 1.1}, "ADJ-SNOW": {"multiplier": None}}
        self.assertEqual(module.weather_adjustment(row, multipliers), 0.7)


if __name__ == "__main__":
    unittest.main()
