# Alternate POS Integration

## Goal

Integrate store-level exports from the alternate POS into the existing load flow without breaking the current Tapshopbar legacy POS path.

## Observed Format

- Workbook: `store_sales_by_product`
- Header row is row 1
- Columns:
  - `상품코드`
  - `매출일자`
  - `바코드`
  - `지점`
  - `카테고리`
  - `상품명`
  - `수량`
  - `총매출액(원)`
  - `할인액(원)`
  - `실매출액`
  - `가액(원)`
  - `부가세(원)`
- Final row is `합계`

## Integration Rules

1. Detect source system before parsing:
   - `tapshopbar_legacy`
   - `store_sales_by_product`
2. For alternate POS:
   - Skip the `합계` row
   - Map `지점 -> store_name`
   - Map `카테고리 -> category_mid`
   - Use rounded integer amounts for `gross_sales`, `discount_amount`, `net_sales`, `supply_amount`, `vat_amount`
3. Generate deterministic `row_key` because the file does not provide one.
4. Build a staging representation before canonical mapping.
5. Use `상품코드` digits as canonical `product_id` when possible.
6. If both `상품코드` and `바코드` are unusable, generate a deterministic negative surrogate id from store/date/product name.

## Staging Schema Proposal

Recommended raw/staging table fields:

- `source_system`
- `sales_date`
- `raw_product_code`
- `barcode`
- `store_name`
- `raw_category`
- `product_name`
- `quantity`
- `gross_sales_raw`
- `discount_amount_raw`
- `net_sales_raw`
- `supply_amount_raw`
- `vat_amount_raw`
- `canonical_product_id`
- `generated_row_key`

## Notes

- This adapter is a safe compatibility layer, not proof that alternate POS product ids are semantically identical to legacy POS product ids.
- If analytics require strict product identity, maintain a product mapping table between systems.
