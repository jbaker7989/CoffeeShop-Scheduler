"""SDD regression specifications for BUG-001 and BUG-003.

Run with: python3 -m unittest data/test_bug_regressions.py
"""
import datetime as dt
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook


ROOT = Path(__file__).resolve().parent


def load_script(name):
    path = ROOT / name
    spec = importlib.util.spec_from_file_location(f"test_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_staff_workbook(path, adult_count, minor_count):
    wb = Workbook()
    ws = wb.active
    ws.title = "Staff"
    headers = ["employee_id", "first_name", "last_name", "sex", "dob",
               "ssn", "line1-address", "line2-address", "city-address",
               "state-address", "zipcode-address"]
    ws.append(headers)
    for number in range(adult_count + minor_count):
        dob = dt.date(1990, 1, 1) if number < adult_count else dt.date(2012, 1, 1)
        ws.append([f"EMP-{number + 1:04d}", "Test", str(number), "X", dob,
                   "000-00-0000", "1 Test Way", None, "New York", "NY", "10001"])
    wb.save(path)


def reset_calibration_basis(path):
    wb = load_workbook(path)
    for sheet_name, key in (("Throughput_Standards", "product_category"),
                            ("Demand_Adjustments", "adjustment_id")):
        ws = wb[sheet_name]
        headers = {cell.value: index + 1 for index, cell in enumerate(ws[1])}
        for row in range(2, ws.max_row + 1):
            if ws.cell(row, headers[key]).value is not None:
                ws.cell(row, headers["basis"], "ASSUMPTION")
    wb.save(path)


class RosterAllocationSpecifications(unittest.TestCase):
    def roster_module(self, tempdir, adults, minors):
        module = load_script("build_roster_fields.py")
        module.STAFF = str(Path(tempdir) / "staff.xlsx")
        module.PRIVDIR = str(Path(tempdir) / "private")
        module.PII_OUT = str(Path(tempdir) / "private" / "staff_pii.xlsx")
        write_staff_workbook(module.STAFF, adults, minors)
        return module

    # spec: F2.ac1
    def test_canonical_partition_allocates_every_employee(self):
        with tempfile.TemporaryDirectory() as tempdir:
            module = self.roster_module(tempdir, adults=32, minors=8)
            module.main()
            ws = load_workbook(module.STAFF, data_only=True)["Staff"]
            headers = {cell.value: index + 1 for index, cell in enumerate(ws[1])}
            self.assertEqual(ws.max_row - 1, 40)
            for row in range(2, ws.max_row + 1):
                self.assertTrue(all(column in headers for column in module.NEW_COLS))
                self.assertIsNotNone(ws.cell(row, headers["role"]).value)

    # spec: F2.ac2
    def test_future_partition_assigns_all_employees_without_key_error(self):
        with tempfile.TemporaryDirectory() as tempdir:
            module = self.roster_module(tempdir, adults=33, minors=7)
            module.main()
            ws = load_workbook(module.STAFF, data_only=True)["Staff"]
            headers = {cell.value: index + 1 for index, cell in enumerate(ws[1])}
            self.assertEqual(ws.max_row - 1, 40)
            for row in range(2, ws.max_row + 1):
                self.assertTrue(all(column in headers for column in module.NEW_COLS))
                self.assertIsNotNone(ws.cell(row, headers["role"]).value)

    # spec: F2.ac3
    def test_infeasible_adult_partition_fails_before_workbook_mutation(self):
        with tempfile.TemporaryDirectory() as tempdir:
            module = self.roster_module(tempdir, adults=14, minors=8)
            before = Path(module.STAFF).read_bytes()
            with self.assertRaisesRegex(module.RosterAllocationError, "adult.*minimum"):
                module.main()
            self.assertEqual(Path(module.STAFF).read_bytes(), before)


class CalibrationIdempotencySpecifications(unittest.TestCase):
    def calibration_module(self, tempdir, reset_basis=True):
        module = load_script("calibrate_standards.py")
        module.STD = str(Path(tempdir) / "standards.xlsx")
        shutil.copy2(ROOT / "coffee_shop_labor_standards.xlsx", module.STD)
        if reset_basis:
            reset_calibration_basis(module.STD)
        return module

    # spec: F5.ac1
    def test_uncalibrated_workbook_calibrates_once_and_marks_rows(self):
        with tempfile.TemporaryDirectory() as tempdir:
            module = self.calibration_module(tempdir)
            module.main()
            wb = load_workbook(module.STD, data_only=True)
            ws = wb["Throughput_Standards"]
            headers = {cell.value: index for index, cell in enumerate(ws[1])}
            bases = [row[headers["basis"]] for row in ws.iter_rows(min_row=2, values_only=True)
                     if row[0] is not None]
            self.assertTrue(all(basis == "CALIBRATED" for basis in bases))

    # spec: F5.ac2
    def test_second_calibration_refuses_before_mutating_workbook(self):
        with tempfile.TemporaryDirectory() as tempdir:
            module = self.calibration_module(tempdir)
            module.main()
            before = Path(module.STD).read_bytes()
            with self.assertRaisesRegex(module.CalibrationAlreadyAppliedError, "already calibrated"):
                module.main()
            self.assertEqual(Path(module.STD).read_bytes(), before)

    # spec: F5.ac3
    def test_mixed_calibration_state_refuses_before_mutating_workbook(self):
        with tempfile.TemporaryDirectory() as tempdir:
            module = self.calibration_module(tempdir)
            wb = load_workbook(module.STD)
            ws = wb["Throughput_Standards"]
            headers = {cell.value: index + 1 for index, cell in enumerate(ws[1])}
            ws.cell(2, headers["basis"], "CALIBRATED")
            wb.save(module.STD)
            before = Path(module.STD).read_bytes()
            with self.assertRaisesRegex(module.CalibrationAlreadyAppliedError, "already calibrated"):
                module.main()
            self.assertEqual(Path(module.STD).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
