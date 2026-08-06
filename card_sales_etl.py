from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import BinaryIO

import pandas as pd

from etl import PosLoaderError, normalize_header, parse_date_value, parse_int_value


OKPOS_CARD_SOURCE_SYSTEM = "okpos_card_daily_summary"

OKPOS_CARD_REQUIRED_COLUMNS = [
    "일자",
    "매장명",
    "총매출",
    "실매출",
    "결제합계",
    "신용카드",
]

CARD_TARGET_COLUMNS = [
    "row_key",
    "sales_date",
    "store_name",
    "source_system",
    "card_sales_amount",
    "gross_sales",
    "net_sales",
    "payment_total",
    "source_file_name",
    "loaded_at",
]


@dataclass
class OkposCardDetectionResult:
    sheet_name: str
    header_row_index: int
    matched_columns: list[str]


@dataclass
class CardTransformationResult:
    source_frame: pd.DataFrame
    transformed_frame: pd.DataFrame
    detection: OkposCardDetectionResult


def read_okpos_card_source(
    source: str | Path | BinaryIO,
) -> tuple[pd.DataFrame, OkposCardDetectionResult]:
    workbook = pd.read_excel(source, sheet_name=None, header=None, dtype=object)
    expected = set(OKPOS_CARD_REQUIRED_COLUMNS)
    best_match: tuple[str, int, list[str]] | None = None

    for sheet_name, frame in workbook.items():
        for row_index in range(len(frame.index)):
            headers = [normalize_header(value) for value in frame.iloc[row_index].tolist()]
            matches = [column for column in OKPOS_CARD_REQUIRED_COLUMNS if column in headers]
            if best_match is None or len(matches) > len(best_match[2]):
                best_match = (sheet_name, row_index, matches)
            if expected.issubset(set(headers)):
                data = frame.iloc[row_index + 1 :].copy()
                data.columns = headers
                data = data.loc[:, [column for column in data.columns if column]]
                data = data.dropna(how="all").reset_index(drop=True)
                return data, OkposCardDetectionResult(sheet_name, row_index, matches)

    if best_match is None:
        raise PosLoaderError("OK POS 카드매출 파일에서 헤더 행을 찾지 못했습니다.")

    missing = [column for column in OKPOS_CARD_REQUIRED_COLUMNS if column not in best_match[2]]
    raise PosLoaderError(
        "OK POS 카드매출 헤더의 필수 컬럼이 부족합니다. "
        f"시트='{best_match[0]}', 헤더 행={best_match[1] + 1}, 누락 컬럼={missing}"
    )


def build_okpos_card_row_key(sales_date: object, store_name: str) -> str:
    seed = f"{OKPOS_CARD_SOURCE_SYSTEM}|{sales_date}|{store_name}"
    digest = sha1(seed.encode("utf-8")).hexdigest()[:20]
    return f"okpos-card::{digest}"


def transform_okpos_card_dataframe(
    frame: pd.DataFrame,
    source_file_name: str,
    loaded_at: datetime | None = None,
) -> pd.DataFrame:
    missing = [column for column in OKPOS_CARD_REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise PosLoaderError("OK POS 카드매출 필수 컬럼이 누락되었습니다: " + ", ".join(missing))

    row_labels = frame["일자"].map(lambda value: "" if pd.isna(value) else str(value).strip())
    detail = frame.loc[
        ~row_labels.str.startswith("소계:") & ~row_labels.eq("합계") & ~row_labels.eq("")
    ].copy()
    detail = detail.loc[detail["매장명"].notna()].reset_index(drop=True)

    transformed = pd.DataFrame()
    transformed["sales_date"] = detail["일자"].map(parse_date_value)
    transformed["store_name"] = detail["매장명"].map(
        lambda value: "" if pd.isna(value) else str(value).strip()
    )

    invalid_dates = transformed["sales_date"].isna()
    if invalid_dates.any():
        stores = transformed.loc[invalid_dates, "store_name"].head(5).tolist()
        raise PosLoaderError(f"OK POS 카드매출의 일자 변환에 실패했습니다. 예시 매장={stores}")

    empty_stores = transformed["store_name"].eq("")
    if empty_stores.any():
        raise PosLoaderError("OK POS 카드매출에 매장명이 비어 있는 상세 행이 있습니다.")

    transformed["source_system"] = OKPOS_CARD_SOURCE_SYSTEM
    transformed["card_sales_amount"] = detail["신용카드"].map(parse_int_value)
    transformed["gross_sales"] = detail["총매출"].map(parse_int_value)
    transformed["net_sales"] = detail["실매출"].map(parse_int_value)
    transformed["payment_total"] = detail["결제합계"].map(parse_int_value)
    transformed["source_file_name"] = source_file_name
    transformed["loaded_at"] = pd.Timestamp(loaded_at or datetime.now(timezone.utc))
    transformed["row_key"] = [
        build_okpos_card_row_key(sales_date, store_name)
        for sales_date, store_name in zip(
            transformed["sales_date"],
            transformed["store_name"],
        )
    ]

    numeric_columns = ["card_sales_amount", "gross_sales", "net_sales", "payment_total"]
    for column in numeric_columns:
        invalid = transformed[column].isna()
        if invalid.any():
            stores = transformed.loc[invalid, "store_name"].head(5).tolist()
            raise PosLoaderError(f"{column} 값을 숫자로 변환할 수 없습니다. 예시 매장={stores}")
        transformed[column] = transformed[column].astype("Int64")

    if transformed["row_key"].duplicated().any():
        duplicates = transformed.loc[transformed["row_key"].duplicated(False), "store_name"].head(5).tolist()
        raise PosLoaderError(f"파일 안에 동일 매장·매출일 행이 중복되어 있습니다. 예시 매장={duplicates}")

    transformed["row_key"] = transformed["row_key"].astype("string")
    transformed["store_name"] = transformed["store_name"].astype("string")
    transformed["source_system"] = transformed["source_system"].astype("string")
    transformed["source_file_name"] = transformed["source_file_name"].astype("string")
    return transformed[CARD_TARGET_COLUMNS].reset_index(drop=True)


def transform_okpos_card_source(
    source: str | Path | BinaryIO,
    source_file_name: str | None = None,
) -> CardTransformationResult:
    source_frame, detection = read_okpos_card_source(source)
    if source_file_name is None:
        source_file_name = Path(source).name if isinstance(source, (str, Path)) else "uploaded.xls"
    transformed = transform_okpos_card_dataframe(source_frame, source_file_name)
    return CardTransformationResult(source_frame, transformed, detection)
