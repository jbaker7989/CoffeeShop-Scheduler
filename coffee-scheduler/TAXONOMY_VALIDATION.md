# Product Taxonomy — Validation Report (CS-009 / F29)

Generated: 2026-11-12 19:00:19 by `data/validate_taxonomy.py`.
Overall: **PASS** (5/5 checks passed).

## ✅ A. Complete mapping (one row per product_id, no nulls) — PASS
- Columns present: ['product_category', 'product_detail', 'product_id', 'product_type', 'taxonomy_version'] (all present).
- Rows: 80; sales product_ids: 80; match: True; duplicate ids: 0.
- Null hierarchy cells: none.

## ✅ B. product_id -> category is single-valued — PASS
- product_ids mapping to >1 category: none.

## ✅ C. No drift vs source (taxonomy categories == sales categories) — PASS
- In sales but missing from taxonomy: none.
- In taxonomy but not in sales (invented): none.

## ✅ D. Cross-consumer coverage (taxonomy categories == Throughput_Standards) — PASS
- In taxonomy only: none.
- In Throughput_Standards only: none.

## ✅ E. Pinned version stamped everywhere + counts consistent — PASS
- Taxonomy_Meta version: 'TAX-2023H1-v1'; source_period: '2023-01-01..2023-06-30'.
- Distinct versions stamped on rows: ['TAX-2023H1-v1'] (expect exactly ['TAX-2023H1-v1']).
- Meta counts match data (n_products, n_categories): True.
