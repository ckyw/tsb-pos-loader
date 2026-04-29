from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd
from dotenv import load_dotenv
from google.cloud import bigquery
from google.cloud.bigquery import ArrayQueryParameter, QueryJobConfig

from etl import PosLoaderError, TARGET_COLUMNS, parse_date_value, parse_int_value


REQUIRED_TABLE_TYPES = {
    "row_key": "STRING",
    "sales_date": "DATE",
    "product_id": "INTEGER",
    "store_name": "STRING",
    "category_mid": "STRING",
    "product_name": "STRING",
    "quantity": "INTEGER",
    "gross_sales": "INTEGER",
    "discount_amount": "INTEGER",
    "net_sales": "INTEGER",
    "supply_amount": "INTEGER",
    "vat_amount": "INTEGER",
}


def resolve_target_table() -> tuple[str, str, str, str]:
    load_dotenv()
    project_id = os.getenv("GCP_PROJECT_ID", "").strip()
    dataset = os.getenv("BIGQUERY_DATASET", "").strip()
    table = os.getenv("BIGQUERY_TABLE", "").strip()
    missing = [
        name
        for name, value in (
            ("GCP_PROJECT_ID", project_id),
            ("BIGQUERY_DATASET", dataset),
            ("BIGQUERY_TABLE", table),
        )
        if not value
    ]
    if missing:
        raise PosLoaderError("환경변수가 설정되지 않았습니다: " + ", ".join(missing))

    if "." in dataset:
        raise PosLoaderError(
            "BIGQUERY_DATASET에는 dataset 이름만 넣어야 합니다. "
            f"현재 값='{dataset}', 예시='okpos_sales'"
        )
    if "." in table:
        raise PosLoaderError(
            "BIGQUERY_TABLE에는 table 이름만 넣어야 합니다. "
            f"현재 값='{table}', 예시='okpos_0301'"
        )
    return project_id, dataset, table, f"{project_id}.{dataset}.{table}"


def get_bigquery_client(project_id: str | None = None) -> bigquery.Client:
    resolved_project_id, _, _, _ = resolve_target_table()
    return bigquery.Client(project=project_id or resolved_project_id)


def get_target_table(
    client: bigquery.Client | None = None,
) -> tuple[bigquery.Client, bigquery.Table, str]:
    project_id, _, _, table_fqn = resolve_target_table()
    bq_client = client or bigquery.Client(project=project_id)
    table = bq_client.get_table(table_fqn)
    validate_table_schema(table)
    return bq_client, table, table_fqn


def validate_table_schema(table: bigquery.Table) -> None:
    table_columns = [field.name for field in table.schema]
    if table_columns != TARGET_COLUMNS:
        raise PosLoaderError(
            "BigQuery 테이블 컬럼 순서 또는 이름이 기대와 다릅니다. "
            f"expected={TARGET_COLUMNS}, actual={table_columns}"
        )
    for field in table.schema:
        expected_type = REQUIRED_TABLE_TYPES[field.name]
        if field.field_type.upper() != expected_type:
            raise PosLoaderError(
                f"BigQuery 컬럼 타입 불일치: {field.name} "
                f"(expected={expected_type}, actual={field.field_type})"
            )


def coerce_dataframe_to_table_schema(
    dataframe: pd.DataFrame,
    table: bigquery.Table,
) -> pd.DataFrame:
    coerced = dataframe.copy()
    for field in table.schema:
        column = field.name
        field_type = field.field_type.upper()

        if field_type == "STRING":
            coerced[column] = coerced[column].astype("string").fillna("")
        elif field_type == "INTEGER":
            parsed = coerced[column].map(parse_int_value)
            invalid_mask = parsed.isna()
            if invalid_mask.any():
                sample_keys = coerced.loc[invalid_mask, "row_key"].astype(str).head(5).tolist()
                raise PosLoaderError(
                    f"{column} 컬럼에 숫자로 변환할 수 없는 값 또는 빈 값이 있습니다. "
                    f"예시 row_key={sample_keys}"
                )
            coerced[column] = pd.to_numeric(parsed, errors="raise").astype("int64")
        elif field_type == "DATE":
            coerced[column] = coerced[column].map(parse_date_value)
    return coerced[TARGET_COLUMNS]


def chunked(values: Sequence[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield list(values[index : index + size])


def fetch_existing_row_keys(
    client: bigquery.Client,
    table_fqn: str,
    row_keys: Sequence[str],
) -> set[str]:
    existing: set[str] = set()
    for batch in chunked(row_keys, 5000):
        query = f"""
            SELECT row_key
            FROM `{table_fqn}`
            WHERE row_key IN UNNEST(@row_keys)
        """
        job = client.query(
            query,
            job_config=QueryJobConfig(
                query_parameters=[ArrayQueryParameter("row_keys", "STRING", batch)]
            ),
        )
        existing.update(row.row_key for row in job.result())
    return existing


def count_existing_row_keys(dataframe: pd.DataFrame) -> int:
    client, _, table_fqn = get_target_table()
    row_keys = dataframe["row_key"].astype(str).tolist()
    return len(fetch_existing_row_keys(client, table_fqn, row_keys))


def delete_existing_row_keys(
    client: bigquery.Client,
    table_fqn: str,
    row_keys: Sequence[str],
) -> int:
    deleted = 0
    for batch in chunked(row_keys, 5000):
        query = f"""
            DELETE FROM `{table_fqn}`
            WHERE row_key IN UNNEST(@row_keys)
        """
        job = client.query(
            query,
            job_config=QueryJobConfig(
                query_parameters=[ArrayQueryParameter("row_keys", "STRING", batch)]
            ),
        )
        job.result()
        deleted += len(batch)
    return deleted


def load_to_bigquery(
    dataframe: pd.DataFrame,
    duplicate_strategy: str,
) -> tuple[int, int, int]:
    client, table, table_fqn = get_target_table()
    dataframe = coerce_dataframe_to_table_schema(dataframe, table)

    row_keys = dataframe["row_key"].astype(str).tolist()
    existing_row_keys = fetch_existing_row_keys(client, table_fqn, row_keys)

    deleted_count = 0
    if duplicate_strategy == "skip":
        dataframe = dataframe.loc[~dataframe["row_key"].isin(existing_row_keys)].copy()
    elif duplicate_strategy == "replace" and existing_row_keys:
        deleted_count = delete_existing_row_keys(client, table_fqn, sorted(existing_row_keys))

    if dataframe.empty:
        return 0, len(existing_row_keys), deleted_count

    job_config = bigquery.LoadJobConfig(
        schema=table.schema,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )

    try:
        load_job = client.load_table_from_dataframe(dataframe, table_fqn, job_config=job_config)
        load_job.result()
    except Exception as exc:
        message = str(exc)
        if "Error converting Pandas column" not in message:
            raise

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".csv",
            newline="",
            encoding="utf-8",
            delete=False,
        ) as temp_file:
            csv_path = Path(temp_file.name)
            dataframe.to_csv(
                temp_file,
                index=False,
                quoting=csv.QUOTE_MINIMAL,
                date_format="%Y-%m-%d",
            )

        try:
            with csv_path.open("rb") as csv_file:
                fallback_job = client.load_table_from_file(
                    csv_file,
                    table_fqn,
                    job_config=bigquery.LoadJobConfig(
                        schema=table.schema,
                        source_format=bigquery.SourceFormat.CSV,
                        skip_leading_rows=1,
                        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                    ),
                )
                fallback_job.result()
        finally:
            csv_path.unlink(missing_ok=True)

    return len(dataframe), len(existing_row_keys), deleted_count
