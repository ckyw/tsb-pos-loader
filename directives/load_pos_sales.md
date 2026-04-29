# Tapshopbar POS to BigQuery

## Goal

Read a Tapshopbar POS Excel file, normalize it to the fixed BigQuery schema, and append only valid rows into the existing target table.

## Inputs

- POS `.xlsx` file path
- Environment variables:
  - `GCP_PROJECT_ID`
  - `BIGQUERY_DATASET`
  - `BIGQUERY_TABLE`
- CLI mode:
  - `--dry-run`
  - `--load`
- Duplicate strategy:
  - `skip`
  - `replace`

## Outputs

- A pandas DataFrame with this exact column order:
  - `row_key`
  - `sales_date`
  - `product_id`
  - `store_name`
  - `category_mid`
  - `product_name`
  - `quantity`
  - `gross_sales`
  - `discount_amount`
  - `net_sales`
  - `supply_amount`
  - `vat_amount`
- Optional BigQuery append load result

## SOP

1. Detect the header row by scanning every sheet for the expected Korean source headers.
2. Remove blank rows and total rows such as `합계`, `총계`.
3. Keep only the source columns mapped to the existing BigQuery schema.
4. Normalize types:
   - `sales_date` -> DATE-compatible
   - `row_key`, `product_id`, text fields -> STRING
   - `quantity`, amount fields -> INTEGER
5. In `--dry-run`, print detection info and a preview only.
6. In `--load`, validate the existing BigQuery schema before append.
7. Deduplicate by `row_key`:
   - `skip`: do not append existing keys
   - `replace`: delete existing keys, then append the new rows

## Edge Cases

- Header row not at the first row
- Multiple sheets
- Empty rows between data blocks
- Currency text such as commas, `₩`, `원`, spaces
- Missing required source columns
- Invalid `sales_date`
- Empty `row_key`
- Existing BigQuery schema mismatch
