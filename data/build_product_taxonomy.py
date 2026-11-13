"""CS-009 (F29): build the versioned product taxonomy into coffee_shop_labor_standards.xlsx.

Derives the single source-of-truth mapping product_id -> (detail, type, category) from
coffee_shop_sales.xlsx and writes two sheets, co-located with Throughput_Standards:
  - Product_Taxonomy : one row per product_id, stamped with taxonomy_version.
  - Taxonomy_Meta    : one row pinning the version + provenance.

Idempotent (D4 in test/SKILL.md): rebuilds both sheets from the immutable sales source on every run,
so re-running yields identical sheet content regardless of prior state. Deterministic — no wall-clock,
no randomness. All other sheets in the workbook are preserved untouched.

Run:  python3 build_product_taxonomy.py      (needs: pandas, openpyxl)
Reads (read-only): coffee_shop_sales.xlsx.  Writes: coffee_shop_labor_standards.xlsx.
"""
import os
import datetime as dt
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill

HERE = os.path.dirname(os.path.abspath(__file__))
STD = os.path.join(HERE, "coffee_shop_labor_standards.xlsx")
SALES = os.path.join(HERE, "coffee_shop_sales.xlsx")

VERSION = "TAX-2023H1-v1"               # pinned; bump when the source hierarchy changes
GENERATED_ON = dt.date(2026, 11, 12)    # frozen, not wall-clock, so the sheet is reproducible
HEADER_FILL = PatternFill("solid", fgColor="5B3A29")
HEADER_FONT = Font(name="Arial", size=10, bold=True, color="FFFFFF")

TAX_COLS = ["product_id", "product_detail", "product_type", "product_category", "taxonomy_version"]
META_COLS = ["taxonomy_version", "source_period", "generated_on", "n_products", "n_types", "n_categories"]


def build_mapping(sales):
    """Distinct product_id -> detail/type/category, asserting the hierarchy is single-valued (AC2)."""
    m = sales[["product_id", "product_detail", "product_type", "product_category"]].drop_duplicates()
    for level in ("product_category", "product_type", "product_detail"):
        multi = m.groupby("product_id")[level].nunique()
        bad = sorted(int(i) for i in multi[multi > 1].index)
        if bad:
            raise SystemExit(f"product_id maps to >1 {level}: {bad} — source hierarchy is not a function")
    return m.sort_values("product_id").reset_index(drop=True)


def write_sheet(wb, name, header, rows):
    if name in wb.sheetnames:
        del wb[name]                      # full rebuild -> idempotent, no stale rows
    ws = wb.create_sheet(name)
    ws.append(header)
    for c in ws[1]:
        c.font, c.fill = HEADER_FONT, HEADER_FILL
    for r in rows:
        ws.append(r)
    ws.freeze_panes = "A2"
    return ws


def main():
    sales = pd.read_excel(SALES)
    m = build_mapping(sales)
    d = pd.to_datetime(sales.transaction_date)
    period = f"{d.min().date()}..{d.max().date()}"

    wb = load_workbook(STD)
    tax_rows = [[int(r.product_id), r.product_detail, r.product_type, r.product_category, VERSION]
                for r in m.itertuples(index=False)]
    write_sheet(wb, "Product_Taxonomy", TAX_COLS, tax_rows)
    meta_row = [VERSION, period, GENERATED_ON,
                int(m.product_id.nunique()), int(m.product_type.nunique()), int(m.product_category.nunique())]
    write_sheet(wb, "Taxonomy_Meta", META_COLS, [meta_row])
    wb.save(STD)

    print(f"Wrote Product_Taxonomy ({len(tax_rows)} rows) + Taxonomy_Meta to {STD}")
    print(f"  version={VERSION}  source={period}  "
          f"products={meta_row[3]} types={meta_row[4]} categories={meta_row[5]}")


if __name__ == "__main__":
    main()
