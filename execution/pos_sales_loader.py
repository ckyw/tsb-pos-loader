from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd
from dotenv import load_dotenv
from google.cloud import bigquery
from google.cloud.bigquery import ArrayQueryParameter, QueryJobConfig, SchemaField


EXPECTED_SOURCE_COLUMNS = [
    "매장코드_매출일자_상품코드",
    "매출일자",
    "상품코드",
    "지점",
    "중분류",
    "상품명",
    "수량",
    "총매출액",
    "할인액",
    "실매출액",
    "가액",
    "부가세",
]

TARGET_COLUMNS = [
    "row_key",
    "sales_date",
    "product_id",
    "store_name",
    "category_mid",
    "product_name",
    "quantity",
    "gross_sales",
    "discount_amount",
    "net_sales",
    "supply_amount",
    "vat_amount",
]

SOURCE_TO_TARGET = {
    "매장코드_매출일자_상품코드": "row_key",
    "매출일자": "sales_date",
    "상품코드": "product_id",
    "지점": "store_name",
    "중분류": "category_mid",
    "상품명": "product_name",
    "수량": "quantity",
    "총매출액": "gross_sales",
    "할인액": "discount_amount",
    "실매출액": "net_sales",
    "가액": "supply_amount",
    "부가세": "vat_amount",
}

INTEGER_COLUMNS = [
    "quantity",
    "gross_sales",
    "discount_amount",
    "net_sales",
    "supply_amount",
    "vat_amount",
]

STRING_COLUMNS = [
    "row_key",
    "product_id",
    "store_name",
    "category_mid",
    "product_name",
]

TOTAL_ROW_MARKERS = ("합계", "총계")
REQUIRED_TABLE_TYPES = {
    "row_key": "STRING",
    "sales_date": "DATE",
    "product_id": "STRING",
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


class PosLoaderError(Exception):
    pass


@dataclass
class HeaderDetectionResult:
    sheet_name: str
    header_row_index: int
    matched_columns: list[str]
    raw_frame: pd.DataFrame


def normalize_header(value: object) -> str:
    if value is None:
        return ""
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "".join(text.split()).replace("\n", "")


def detect_header_row(workbook: dict[str, pd.DataFrame]) -> HeaderDetectionResult:
    best_result: HeaderDetectionResult | None = None
    best_score = -1
    expected = {normalize_header(column): column for column in EXPECTED_SOURCE_COLUMNS}

    for sheet_name, frame in workbook.items():
        for row_index in range(len(frame.index)):
            row = frame.iloc[row_index].tolist()
            normalized_row = [normalize_header(cell) for cell in row]
            matches = [expected[value] for value in normalized_row if value in expected]
            score = len(set(matches))
            if score > best_score:
                best_score = score
                best_result = HeaderDetectionResult(
                    sheet_name=sheet_name,
                    header_row_index=row_index,
                    matched_columns=sorted(set(matches), key=EXPECTED_SOURCE_COLUMNS.index),
                    raw_frame=frame,
                )
            if score == len(EXPECTED_SOURCE_COLUMNS):
                return best_result

    if best_result is None or best_score <= 0:
        raise PosLoaderError(
            "엑셀에서 헤더 행을 찾지 못했습니다. "
            f"필수 컬럼: {', '.join(EXPECTED_SOURCE_COLUMNS)}"
        )

    missing = [column for column in EXPECTED_SOURCE_COLUMNS if column not in best_result.matched_columns]
    raise PosLoaderError(
        "엑셀 헤더는 찾았지만 필수 컬럼이 부족합니다. "
        f"시트='{best_result.sheet_name}', 헤더 행={best_result.header_row_index + 1}, "
        f"누락 컬럼={missing}"
    )


def read_source_dataframe(file_path: Path) -> tuple[pd.DataFrame, HeaderDetectionResult]:
    workbook = pd.read_excel(file_path, sheet_name=None, header=None, dtype=object)
    detection = detect_header_row(workbook)
    frame = detection.raw_frame.copy()
    raw_headers = frame.iloc[detection.header_row_index].tolist()
    headers = [normalize_header(value) for value in raw_headers]
    data = frame.iloc[detection.header_row_index + 1 :].copy()
    data.columns = headers
    data = data.loc[:, [column for column in data.columns if column]]
    data = data.dropna(how="all").reset_index(drop=True)
    return data, detection


def parse_date_value(value: object) -> object:
    if value is None or (isinstance(value, float) and pd.isna(value)) or pd.isna(value):
        return pd.NaT
    if isinstance(value, pd.Timestamp):
        return value.date()
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        return pd.Timestamp(value).date()
    if isinstance(value, (int, float)):
        try:
            return pd.to_datetime(value, unit="D", origin="1899-12-30").date()
        except (ValueError, TypeError, OverflowError):
            pass
    text = str(value).strip()
    if not text:
        return pd.NaT
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return pd.NaT
    return parsed.date()


def parse_int_value(value: object) -> int | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    cleaned = (
        text.replace(",", "")
        .replace("원", "")
        .replace("₩", "")
        .replace("\\", "")
        .replace(" ", "")
        .replace("(", "")
        .replace(")", "")
    )
    cleaned = "".join(character for character in cleaned if character.isdigit() or character == "-")
    if cleaned in {"", "-"}:
        return None
    amount = int(cleaned)
    return -amount if negative and amount > 0 else amount


def remove_total_and_empty_rows(frame: pd.DataFrame) -> pd.DataFrame:
    working = frame.copy()
    working = working.dropna(how="all")
    non_key_columns = [column for column in EXPECTED_SOURCE_COLUMNS if column in working.columns]
    if non_key_columns:
        working = working.loc[
            ~working[non_key_columns]
            .apply(lambda row: all(str(value).strip() == "" or pd.isna(value) for value in row), axis=1)
        ]

    def is_total_row(row: pd.Series) -> bool:
        for column in ("매장코드_매출일자_상품코드", "지점", "중분류", "상품명"):
            value = row.get(column)
            text = "" if value is None or pd.isna(value) else str(value).strip()
            if any(marker in text for marker in TOTAL_ROW_MARKERS):
                return True
        return False

    return working.loc[~working.apply(is_total_row, axis=1)].reset_index(drop=True)


def validate_required_columns(frame: pd.DataFrame) -> None:
    missing = [column for column in EXPECTED_SOURCE_COLUMNS if column not in frame.columns]
    if missing:
        raise PosLoaderError(
            "필수 원본 컬럼이 누락되었습니다: " + ", ".join(missing)
        )


def transform_dataframe(frame: pd.DataFrame) -> pd.DataFrame:
    validate_required_columns(frame)
    cleaned = remove_total_and_empty_rows(frame)
    renamed = cleaned.loc[:, EXPECTED_SOURCE_COLUMNS].rename(columns=SOURCE_TO_TARGET)

    transformed = pd.DataFrame()
    transformed["row_key"] = renamed["row_key"].map(lambda value: "" if pd.isna(value) else str(value).strip())
    transformed["sales_date"] = renamed["sales_date"].map(parse_date_value)
    transformed["product_id"] = renamed["product_id"].map(lambda value: "" if pd.isna(value) else str(value).strip())
    transformed["store_name"] = renamed["store_name"].map(lambda value: "" if pd.isna(value) else str(value).strip())
    transformed["category_mid"] = renamed["category_mid"].map(lambda value: "" if pd.isna(value) else str(value).strip())
    transformed["product_name"] = renamed["product_name"].map(lambda value: "" if pd.isna(value) else str(value).strip())
    transformed["quantity"] = renamed["quantity"].map(parse_int_value)
    transformed["gross_sales"] = renamed["gross_sales"].map(parse_int_value)
    transformed["discount_amount"] = renamed["discount_amount"].map(parse_int_value)
    transformed["net_sales"] = renamed["net_sales"].map(parse_int_value)
    transformed["supply_amount"] = renamed["supply_amount"].map(parse_int_value)
    transformed["vat_amount"] = renamed["vat_amount"].map(parse_int_value)

    transformed = transformed.loc[
        ~(
            transformed["row_key"].eq("")
            & transformed["product_id"].eq("")
            & transformed["product_name"].eq("")
        )
    ].copy()

    invalid_dates = transformed["sales_date"].isna()
    if invalid_dates.any():
        sample_keys = transformed.loc[invalid_dates, "row_key"].head(5).tolist()
        raise PosLoaderError(
            "sales_date 변환에 실패한 행이 있습니다. "
            f"예시 row_key={sample_keys}"
        )

    empty_keys = transformed["row_key"].eq("")
    if empty_keys.any():
        raise PosLoaderError("row_key가 비어 있는 행이 있어 적재할 수 없습니다.")

    for column in STRING_COLUMNS:
        transformed[column] = transformed[column].astype("string").fillna("")

    for column in INTEGER_COLUMNS:
        transformed[column] = transformed[column].astype("Int64")

    transformed = transformed[TARGET_COLUMNS].reset_index(drop=True)
    return transformed


def bigquery_schema() -> list[SchemaField]:
    return [
        SchemaField("row_key", "STRING"),
        SchemaField("sales_date", "DATE"),
        SchemaField("product_id", "STRING"),
        SchemaField("store_name", "STRING"),
        SchemaField("category_mid", "STRING"),
        SchemaField("product_name", "STRING"),
        SchemaField("quantity", "INTEGER"),
        SchemaField("gross_sales", "INTEGER"),
        SchemaField("discount_amount", "INTEGER"),
        SchemaField("net_sales", "INTEGER"),
        SchemaField("supply_amount", "INTEGER"),
        SchemaField("vat_amount", "INTEGER"),
    ]


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


def resolve_target_table() -> tuple[str, str, str, str]:
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
        raise PosLoaderError(
            "환경변수가 설정되지 않았습니다: " + ", ".join(missing)
        )
    return project_id, dataset, table, f"{project_id}.{dataset}.{table}"


def load_to_bigquery(
    dataframe: pd.DataFrame,
    duplicate_strategy: str,
) -> tuple[int, int, int]:
    project_id, _, _, table_fqn = resolve_target_table()
    client = bigquery.Client(project=project_id)
    table = client.get_table(table_fqn)
    validate_table_schema(table)

    row_keys = dataframe["row_key"].astype(str).tolist()
    existing_row_keys = fetch_existing_row_keys(client, table_fqn, row_keys)

    deleted_count = 0
    if duplicate_strategy == "skip":
        dataframe = dataframe.loc[~dataframe["row_key"].isin(existing_row_keys)].copy()
    elif duplicate_strategy == "replace" and existing_row_keys:
        deleted_count = delete_existing_row_keys(client, table_fqn, sorted(existing_row_keys))

    if dataframe.empty:
        return 0, len(existing_row_keys), deleted_count

    load_job = client.load_table_from_dataframe(
        dataframe,
        table_fqn,
        job_config=bigquery.LoadJobConfig(
            schema=bigquery_schema(),
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        ),
    )
    load_job.result()
    return len(dataframe), len(existing_row_keys), deleted_count


def print_dry_run_summary(
    dataframe: pd.DataFrame,
    detection: HeaderDetectionResult,
    file_path: Path,
) -> None:
    print(f"[source] file={file_path}")
    print(f"[source] detected_sheet={detection.sheet_name}")
    print(f"[source] detected_header_row={detection.header_row_index + 1}")
    print(f"[source] matched_columns={', '.join(detection.matched_columns)}")
    print(f"[result] transformed_rows={len(dataframe)}")
    print(f"[result] columns={TARGET_COLUMNS}")
    if not dataframe.empty:
        print("[preview]")
        print(dataframe.head(10).to_string(index=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load Tapshopbar POS Excel files into an existing BigQuery table."
    )
    parser.add_argument("--file", required=True, help="Path to the POS .xlsx file")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Transform and preview rows without loading to BigQuery")
    mode.add_argument("--load", action="store_true", help="Load transformed rows into BigQuery")
    parser.add_argument(
        "--duplicate-strategy",
        choices=("skip", "replace"),
        default="skip",
        help="How to handle row_keys that already exist in BigQuery",
    )
    return parser


def main() -> None:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()

    if not args.dry_run and not args.load:
        parser.error("하나의 실행 모드를 선택하세요: --dry-run 또는 --load")

    file_path = Path(args.file)
    if not file_path.exists():
        raise SystemExit(f"입력 파일을 찾을 수 없습니다: {file_path}")

    try:
        source_frame, detection = read_source_dataframe(file_path)
        transformed = transform_dataframe(source_frame)
        print_dry_run_summary(transformed, detection, file_path)

        if args.load:
            loaded_count, duplicate_count, deleted_count = load_to_bigquery(
                transformed,
                duplicate_strategy=args.duplicate_strategy,
            )
            print(f"[bigquery] duplicate_strategy={args.duplicate_strategy}")
            print(f"[bigquery] existing_row_keys={duplicate_count}")
            if args.duplicate_strategy == "replace":
                print(f"[bigquery] deleted_existing_rows={deleted_count}")
            print(f"[bigquery] loaded_rows={loaded_count}")
    except PosLoaderError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except Exception as exc:
        print(f"[fatal] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
