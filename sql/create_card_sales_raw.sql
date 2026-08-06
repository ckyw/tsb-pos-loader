-- Replace the project and dataset placeholders before running in BigQuery.
CREATE TABLE IF NOT EXISTS `your_project.your_dataset.card_sales_raw` (
  row_key STRING NOT NULL,
  sales_date DATE NOT NULL,
  store_name STRING NOT NULL,
  source_system STRING NOT NULL,
  card_sales_amount INT64 NOT NULL,
  gross_sales INT64 NOT NULL,
  net_sales INT64 NOT NULL,
  payment_total INT64 NOT NULL,
  source_file_name STRING NOT NULL,
  loaded_at TIMESTAMP NOT NULL
)
PARTITION BY sales_date
CLUSTER BY source_system, store_name;
