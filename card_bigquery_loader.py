from __future__ import annotations

import os

import pandas as pd
from dotenv import load_dotenv
from google.cloud import bigquery

from bigquery_loader import delete_existing_row_keys, fetch_existing_row_keys
from card_sales_etl import CARD_TARGET_COLUMNS
from etl import PosLoaderError, parse_date_value, parse_int_value


CARD_TABLE_ENV = "CARD_BIGQUERY_TABLE"

CARD_REQUIRED_TABLE_TYPES = {
    "row_key": "STRING",
    "sales_date": "DATE",
    "store_name": "STRING",
    "source_system": "STRING",
    "card_sales_amount": "INTEGER",
    "gross_sales": "INTEGER",
    "net_sales": "INTEGER",
    "payment_total": "INTEGER",
    "source_file_name": "STRING",
    "loaded_at": "TIMESTAMP",
}


def resolve_card_target_table() -> tuple[str, str, str, str]:
    load_dotenv()
    project_id = os.getenv("GCP_PROJECT_ID", "").strip()
    dataset = os.getenv("BIGQUERY_DATASET", "").strip()
    table = os.getenv(CARD_TABLE_ENV, "").strip()
    missing = [
        name
        for name, value in (
            ("GCP_PROJECT_ID", project_id),
            ("BIGQUERY_DATASET", dataset),
            (CARD_TABLE_ENV, table),
        )
        if not value
    ]
    if missing:
        raise PosLoaderError("환경변수가 설정되지 않았습니다: " + ", ".join(missing))
    if "." in dataset or "." in table:
        raise PosLoaderError("BIGQUERY_DATASET과 CARD_BIGQUERY_TABLE에는 이름만 입력해야 합니다.")
    return project_id, dataset, table, f"{project_id}.{dataset}.{table}"


def get_card_target_table() -> tuple[bigquery.Client, bigquery.Table, str]:
    project_id, _, _, table_fqn = resolve_card_target_table()
    client = bigquery.Client(project=project_id)
    table = client.get_table(table_fqn)
    columns = [field.name for field in table.schema]
    if columns != CARD_TARGET_COLUMNS:
        raise PosLoaderError(
            "카드매출 BigQuery 테이블 컬럼 순서 또는 이름이 기대와 다릅니다. "
            f"expected={CARD_TARGET_COLUMNS}, actual={columns}"
        )
    for field in table.schema:
        expected_type = CARD_REQUIRED_TABLE_TYPES[field.name]
        if field.field_type.upper() != expected_type:
            raise PosLoaderError(
                f"카드매출 BigQuery 컬럼 타입 불일치: {field.name} "
                f"(expected={expected_type}, actual={field.field_type})"
            )
    return client, table, table_fqn


def coerce_card_dataframe(dataframe: pd.DataFrame, table: bigquery.Table) -> pd.DataFrame:
    coerced = dataframe.copy()
    for field in table.schema:
        column = field.name
        field_type = field.field_type.upper()
        if field_type == "STRING":
            coerced[column] = coerced[column].astype("string").fillna("")
        elif field_type == "INTEGER":
            values = coerced[column].map(parse_int_value)
            if values.isna().any():
                raise PosLoaderError(f"{column} 컬럼에 숫자로 변환할 수 없는 값이 있습니다.")
            coerced[column] = pd.to_numeric(values, errors="raise").astype("int64")
        elif field_type == "DATE":
            coerced[column] = coerced[column].map(parse_date_value)
        elif field_type == "TIMESTAMP":
            coerced[column] = pd.to_datetime(coerced[column], utc=True, errors="raise")
    return coerced[CARD_TARGET_COLUMNS]


def count_existing_card_row_keys(dataframe: pd.DataFrame) -> int:
    client, _, table_fqn = get_card_target_table()
    row_keys = dataframe["row_key"].astype(str).tolist()
    return len(fetch_existing_row_keys(client, table_fqn, row_keys))


def load_card_sales_to_bigquery(
    dataframe: pd.DataFrame,
    duplicate_strategy: str,
) -> tuple[int, int, int]:
    client, table, table_fqn = get_card_target_table()
    dataframe = coerce_card_dataframe(dataframe, table)
    row_keys = dataframe["row_key"].astype(str).tolist()
    existing_row_keys = fetch_existing_row_keys(client, table_fqn, row_keys)

    deleted_count = 0
    if duplicate_strategy == "skip":
        dataframe = dataframe.loc[~dataframe["row_key"].isin(existing_row_keys)].copy()
    elif duplicate_strategy == "replace" and existing_row_keys:
        deleted_count = delete_existing_row_keys(client, table_fqn, sorted(existing_row_keys))
    elif duplicate_strategy not in {"skip", "replace"}:
        raise PosLoaderError(f"지원하지 않는 중복 처리 방식입니다: {duplicate_strategy}")

    if dataframe.empty:
        return 0, len(existing_row_keys), deleted_count

    job = client.load_table_from_dataframe(
        dataframe,
        table_fqn,
        job_config=bigquery.LoadJobConfig(
            schema=table.schema,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        ),
    )
    job.result()
    return len(dataframe), len(existing_row_keys), deleted_count
