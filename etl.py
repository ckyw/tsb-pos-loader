from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import pandas as pd


HEADER_ROW_INDEX = 5
DEFAULT_POS_DIR = "pos"
DEFAULT_POS_FILENAME_PREFIX = "tsd"
DEFAULT_POS_EXTENSIONS = (".xlsx", ".xls")

SOURCE_COLUMN_ALIASES = {
    "매장코드_매출일자_상품코드": ("매장코드_매출일자_상품코드",),
    "매출일자": ("매출일자",),
    "상품코드": ("상품코드",),
    "store_name": ("지점", "대분류"),
    "중분류": ("중분류",),
    "상품명": ("상품명",),
    "수량": ("수량",),
    "총매출액": ("총매출액",),
    "할인액": ("할인액",),
    "실매출액": ("실매출액",),
    "가액": ("가액",),
    "부가세": ("부가세",),
}

CANONICAL_SOURCE_COLUMNS = [
    "매장코드_매출일자_상품코드",
    "매출일자",
    "상품코드",
    "store_name",
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
    "store_name": "store_name",
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


def header_alias_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for canonical, aliases in SOURCE_COLUMN_ALIASES.items():
        for alias in aliases:
            lookup[normalize_header(alias)] = canonical
    return lookup


def detect_header_row(workbook: dict[str, pd.DataFrame]) -> HeaderDetectionResult:
    best_result: HeaderDetectionResult | None = None
    best_score = -1
    expected = header_alias_lookup()

    for sheet_name, frame in workbook.items():
        candidate_rows = [HEADER_ROW_INDEX]
        candidate_rows.extend(index for index in range(len(frame.index)) if index != HEADER_ROW_INDEX)

        for row_index in candidate_rows:
            if row_index >= len(frame.index):
                continue
            row = frame.iloc[row_index].tolist()
            normalized_row = [normalize_header(cell) for cell in row]
            matches = [expected[value] for value in normalized_row if value in expected]
            score = len(set(matches))
            if score > best_score:
                best_score = score
                best_result = HeaderDetectionResult(
                    sheet_name=sheet_name,
                    header_row_index=row_index,
                    matched_columns=sorted(set(matches), key=CANONICAL_SOURCE_COLUMNS.index),
                    raw_frame=frame,
                )
            if score == len(CANONICAL_SOURCE_COLUMNS):
                return best_result

    if best_result is None or best_score <= 0:
        raise PosLoaderError(
            "엑셀에서 헤더 행을 찾지 못했습니다. "
            f"필수 컬럼: {', '.join(CANONICAL_SOURCE_COLUMNS)}"
        )

    missing = [column for column in CANONICAL_SOURCE_COLUMNS if column not in best_result.matched_columns]
    raise PosLoaderError(
        "엑셀 헤더는 찾았지만 필수 컬럼이 부족합니다. "
        f"시트='{best_result.sheet_name}', 헤더 행={best_result.header_row_index + 1}, "
        f"누락 컬럼={missing}"
    )


def read_workbook(source: str | Path | BinaryIO) -> dict[str, pd.DataFrame]:
    return pd.read_excel(source, sheet_name=None, header=None, dtype=object)


def read_source_dataframe(source: str | Path | BinaryIO) -> tuple[pd.DataFrame, HeaderDetectionResult]:
    workbook = read_workbook(source)
    detection = detect_header_row(workbook)
    frame = detection.raw_frame.copy()
    raw_headers = frame.iloc[detection.header_row_index].tolist()
    aliases = header_alias_lookup()
    headers = [aliases.get(normalize_header(value), normalize_header(value)) for value in raw_headers]
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
    non_key_columns = [column for column in CANONICAL_SOURCE_COLUMNS if column in working.columns]
    if non_key_columns:
        working = working.loc[
            ~working[non_key_columns]
            .apply(lambda row: all(str(value).strip() == "" or pd.isna(value) for value in row), axis=1)
        ]

    def is_total_row(row: pd.Series) -> bool:
        for column in ("매장코드_매출일자_상품코드", "store_name", "중분류", "상품명"):
            value = row.get(column)
            text = "" if value is None or pd.isna(value) else str(value).strip()
            if any(marker in text for marker in TOTAL_ROW_MARKERS):
                return True
        return False

    return working.loc[~working.apply(is_total_row, axis=1)].reset_index(drop=True)


def validate_required_columns(frame: pd.DataFrame) -> None:
    missing = [column for column in CANONICAL_SOURCE_COLUMNS if column not in frame.columns]
    if missing:
        raise PosLoaderError("필수 원본 컬럼이 누락되었습니다: " + ", ".join(missing))


def transform_dataframe(frame: pd.DataFrame) -> pd.DataFrame:
    validate_required_columns(frame)
    cleaned = remove_total_and_empty_rows(frame)
    renamed = cleaned.loc[:, CANONICAL_SOURCE_COLUMNS].rename(columns=SOURCE_TO_TARGET)

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
        raise PosLoaderError("sales_date 변환에 실패한 행이 있습니다. " f"예시 row_key={sample_keys}")

    empty_keys = transformed["row_key"].eq("")
    if empty_keys.any():
        raise PosLoaderError("row_key가 비어 있는 행이 있어 적재할 수 없습니다.")

    for column in STRING_COLUMNS:
        transformed[column] = transformed[column].astype("string").fillna("")

    for column in INTEGER_COLUMNS:
        transformed[column] = transformed[column].astype("Int64")

    transformed = transformed[TARGET_COLUMNS].reset_index(drop=True)
    return transformed


def resolve_input_file(file_arg: str | None, date_arg: str | None, pos_dir_arg: str) -> Path:
    if file_arg:
        return Path(file_arg)
    if not date_arg:
        raise PosLoaderError("--file 또는 --date 중 하나는 반드시 지정해야 합니다.")

    pos_dir = Path(pos_dir_arg)
    candidates = [
        pos_dir / f"{DEFAULT_POS_FILENAME_PREFIX}{date_arg}{extension}"
        for extension in DEFAULT_POS_EXTENSIONS
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    expected_names = ", ".join(str(candidate) for candidate in candidates)
    raise PosLoaderError(
        f"--date {date_arg} 에 해당하는 POS 파일을 찾지 못했습니다. "
        f"확인 경로: {expected_names}"
    )


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
