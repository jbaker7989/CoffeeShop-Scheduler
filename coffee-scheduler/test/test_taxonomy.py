"""CS-009 / F29 — Product→category taxonomy tests (also BUG-002 regression).

SDD/TDD: written before the generator/validator exist, so every assertion that reads the
`Product_Taxonomy` / `Taxonomy_Meta` sheets or imports the calibration coverage guard fails first.

No pytest in this environment — this mirrors the repo's stdlib-assertion-script pattern
(`data/validate_*.py`): plain `assert`, `# spec:` tags, run with `python3`, exit non-zero on failure.
Each test function is named `test_*` and tagged `# spec: F29.acN` so it is also pytest-discoverable and
passes the CI spec-traceability grep.

Run:  python3 coffee-scheduler/test/test_taxonomy.py     (needs: pandas, openpyxl)
"""
import os
import sys
import pandas as pd
from openpyxl import load_workbook

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.normpath(os.path.join(HERE, "..", "..", "data"))
STD = os.path.join(DATA, "coffee_shop_labor_standards.xlsx")
SALES = os.path.join(DATA, "coffee_shop_sales.xlsx")
sys.path.insert(0, DATA)  # so `import calibrate_standards` resolves to data/calibrate_standards.py

HIER = ["product_detail", "product_type", "product_category"]


def _taxonomy_rows():
    wb = load_workbook(STD, data_only=True)
    ws = wb["Product_Taxonomy"]            # fails here until the generator creates the sheet (AC1)
    hdr = [c.value for c in ws[1]]
    return hdr, [dict(zip(hdr, [c.value for c in r])) for r in ws.iter_rows(min_row=2) if r[0].value is not None]


def _throughput_categories():
    wb = load_workbook(STD, data_only=True)
    tp = wb["Throughput_Standards"]
    h = {c.value: i for i, c in enumerate(tp[1])}
    return {r[h["product_category"]] for r in tp.iter_rows(min_row=2, values_only=True)
            if r[h["product_category"]] and not str(r[h["product_category"]]).startswith("(per transaction")}


def _sales_categories():
    return set(pd.read_excel(SALES).product_category.dropna().unique())


# spec: F29.ac1 — one complete row per sales product_id
def test_ac1_complete_mapping():
    sales = pd.read_excel(SALES)
    hdr, rows = _taxonomy_rows()
    assert {"product_id", "product_detail", "product_type", "product_category", "taxonomy_version"} <= set(hdr)
    tax_ids = {r["product_id"] for r in rows}
    assert tax_ids == set(sales.product_id.unique()), "Product_Taxonomy product_ids != sales product_ids"
    assert len(rows) == sales.product_id.nunique() == len(tax_ids), "expected exactly one row per product_id"
    for r in rows:
        for col in HIER:
            assert r[col] not in (None, ""), f"product_id {r['product_id']} has null {col}"


# spec: F29.ac2 — product_id -> product_category is a function
def test_ac2_category_is_a_function():
    _, rows = _taxonomy_rows()
    by_id = {}
    for r in rows:
        by_id.setdefault(r["product_id"], set()).add(r["product_category"])
    multi = {pid: cats for pid, cats in by_id.items() if len(cats) > 1}
    assert not multi, f"product_ids mapping to >1 category: {multi}"


# spec: F29.ac3 — taxonomy category set == sales category set (no drift vs source)
def test_ac3_no_drift_vs_source():
    _, rows = _taxonomy_rows()
    tax = {r["product_category"] for r in rows}
    sales = _sales_categories()
    assert tax == sales, f"missing={sales - tax}, invented={tax - sales}"


# spec: F29.ac4 — cross-consumer coverage (taxonomy == Throughput_Standards)
def test_ac4_cross_consumer_coverage():
    _, rows = _taxonomy_rows()
    tax = {r["product_category"] for r in rows}
    thr = _throughput_categories()
    assert tax == thr, f"in taxonomy only={tax - thr}, in throughput only={thr - tax}"


# spec: F29.ac4 — BUG-002 guard: calibration refuses an unmapped category (fail-closed, not 0 labor)
def test_ac4_calibration_refuses_unknown_category():
    from calibrate_standards import assert_category_coverage  # fails until the guard exists
    # Happy: full coverage -> returns without raising.
    assert_category_coverage({"Coffee", "Tea"}, {"Coffee", "Tea"})
    # Adversarial: a sales category absent from throughput -> SystemExit naming the offender.
    raised = False
    try:
        assert_category_coverage({"Coffee", "Smoothies"}, {"Coffee"})
    except SystemExit as e:
        raised = True
        assert "Smoothies" in str(e), f"error must name the unmapped category, got: {e}"
    assert raised, "expected calibration to fail closed on an unmapped category, not score it 0"


# spec: F29.ac5 — a single version is pinned and stamped on every row
def test_ac5_pinned_version():
    wb = load_workbook(STD, data_only=True)
    meta = wb["Taxonomy_Meta"]             # fails until the generator creates the sheet
    mh = {c.value: i for i, c in enumerate(meta[1])}
    row = next(meta.iter_rows(min_row=2, values_only=True))
    version = row[mh["taxonomy_version"]]
    assert version, "Taxonomy_Meta has no taxonomy_version"
    _, rows = _taxonomy_rows()
    stamped = {r["taxonomy_version"] for r in rows}
    assert stamped == {version}, f"rows carry versions {stamped}, expected all == {version!r}"


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    passed, failed = 0, 0
    print("=" * 64)
    for name, fn in tests:
        try:
            fn()
            print(f"[PASS] {name}")
            passed += 1
        except Exception as e:                           # AssertionError, KeyError, ImportError, etc.
            print(f"[FAIL] {name}: {type(e).__name__}: {e}")
            failed += 1
    print("=" * 64)
    print(f"OVERALL: {'PASS' if not failed else 'FAIL'} ({passed}/{len(tests)} passed)")
    raise SystemExit(0 if not failed else 1)


if __name__ == "__main__":
    main()
