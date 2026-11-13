"""Validate the CS-009 / F29 product taxonomy in coffee_shop_labor_standards.xlsx.

Read-only. Asserts the taxonomy invariants independently of the generator and of the calibration
code's own coverage guard (D3 in test/SKILL.md), so it can actually catch a drift the other code
tolerates. Writes coffee-scheduler/TAXONOMY_VALIDATION.md; exits non-zero on any failure.

Run:  python3 validate_taxonomy.py      (needs: pandas, openpyxl)
"""
import os
import datetime as dt
import pandas as pd
from openpyxl import load_workbook

HERE = os.path.dirname(os.path.abspath(__file__))
STD = os.path.join(HERE, "coffee_shop_labor_standards.xlsx")
SALES = os.path.join(HERE, "coffee_shop_sales.xlsx")
REPORT = os.path.normpath(os.path.join(HERE, "..", "coffee-scheduler", "TAXONOMY_VALIDATION.md"))

HIER = ["product_detail", "product_type", "product_category"]
results = []


def add(name, passed, lines):
    results.append((name, passed, lines if isinstance(lines, list) else [lines]))


def taxonomy_rows(wb):
    ws = wb["Product_Taxonomy"]
    hdr = [c.value for c in ws[1]]
    rows = [dict(zip(hdr, [c.value for c in r])) for r in ws.iter_rows(min_row=2) if r[0].value is not None]
    return hdr, rows


def throughput_categories(wb):
    tp = wb["Throughput_Standards"]
    h = {c.value: i for i, c in enumerate(tp[1])}
    return {r[h["product_category"]] for r in tp.iter_rows(min_row=2, values_only=True)
            if r[h["product_category"]] and not str(r[h["product_category"]]).startswith("(per transaction")}


def main():
    sales = pd.read_excel(SALES)
    sales_ids = set(sales.product_id.unique())
    sales_cats = set(sales.product_category.dropna().unique())
    wb = load_workbook(STD, data_only=True)
    hdr, rows = taxonomy_rows(wb)

    # A. Complete mapping — one row per sales product_id, all hierarchy fields populated (AC1).
    need = {"product_id", "product_detail", "product_type", "product_category", "taxonomy_version"}
    tax_ids = [r["product_id"] for r in rows]
    nulls = [f"{r['product_id']}:{c}" for r in rows for c in HIER if r[c] in (None, "")]
    a_ok = need <= set(hdr) and set(tax_ids) == sales_ids and len(tax_ids) == len(sales_ids) and not nulls
    add("A. Complete mapping (one row per product_id, no nulls)", a_ok, [
        f"Columns present: {sorted(need <= set(hdr) and need or (need - set(hdr)))} "
        f"({'all present' if need <= set(hdr) else 'MISSING'}).",
        f"Rows: {len(tax_ids)}; sales product_ids: {len(sales_ids)}; "
        f"match: {set(tax_ids) == sales_ids}; duplicate ids: {len(tax_ids) - len(set(tax_ids))}.",
        f"Null hierarchy cells: {nulls[:5] or 'none'}.",
    ])

    # B. product_id -> product_category is a function (AC2).
    by_id = {}
    for r in rows:
        by_id.setdefault(r["product_id"], set()).add(r["product_category"])
    multi = {pid: sorted(c) for pid, c in by_id.items() if len(c) > 1}
    add("B. product_id -> category is single-valued", not multi,
        [f"product_ids mapping to >1 category: {multi or 'none'}."])

    # C. No drift vs source — taxonomy categories == sales categories (AC3).
    tax_cats = {r["product_category"] for r in rows}
    add("C. No drift vs source (taxonomy categories == sales categories)", tax_cats == sales_cats, [
        f"In sales but missing from taxonomy: {sorted(sales_cats - tax_cats) or 'none'}.",
        f"In taxonomy but not in sales (invented): {sorted(tax_cats - sales_cats) or 'none'}.",
    ])

    # D. Cross-consumer coverage — taxonomy == Throughput_Standards (AC4 / BUG-002).
    thr_cats = throughput_categories(wb)
    add("D. Cross-consumer coverage (taxonomy categories == Throughput_Standards)", tax_cats == thr_cats, [
        f"In taxonomy only: {sorted(tax_cats - thr_cats) or 'none'}.",
        f"In Throughput_Standards only: {sorted(thr_cats - tax_cats) or 'none'}.",
    ])

    # E. Pinned version present on meta + every row, counts consistent (AC5).
    meta = wb["Taxonomy_Meta"]
    mh = {c.value: i for i, c in enumerate(meta[1])}
    mrow = next(meta.iter_rows(min_row=2, values_only=True))
    version = mrow[mh["taxonomy_version"]]
    stamped = {r["taxonomy_version"] for r in rows}
    counts_ok = (mrow[mh["n_products"]] == len(sales_ids)
                 and mrow[mh["n_categories"]] == len(tax_cats))
    e_ok = bool(version) and stamped == {version} and counts_ok
    add("E. Pinned version stamped everywhere + counts consistent", e_ok, [
        f"Taxonomy_Meta version: {version!r}; source_period: {mrow[mh['source_period']]!r}.",
        f"Distinct versions stamped on rows: {sorted(stamped)} (expect exactly [{version!r}]).",
        f"Meta counts match data (n_products, n_categories): {counts_ok}.",
    ])

    ok = all(p for _, p, _ in results)
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Product Taxonomy — Validation Report (CS-009 / F29)", "",
        f"Generated: {ts} by `data/validate_taxonomy.py`.",
        f"Overall: **{'PASS' if ok else 'FAIL'}** ({sum(p for _, p, _ in results)}/{len(results)} checks passed).", "",
    ]
    for name, passed, detail in results:
        lines.append(f"## {'✅' if passed else '❌'} {name} — {'PASS' if passed else 'FAIL'}")
        lines += [f"- {x}" for x in detail]
        lines.append("")
    with open(REPORT, "w") as f:
        f.write("\n".join(lines))

    print("=" * 64)
    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
        for x in detail:
            print(f"        {x}")
    print("=" * 64)
    print(f"Report -> {REPORT}\nOVERALL: {'PASS' if ok else 'FAIL'}")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
