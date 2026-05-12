from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha1
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
SOURCE_SYSTEM_LEGACY = "tapshopbar_legacy"
SOURCE_SYSTEM_ALTERNATE = "store_sales_by_product"

ALTERNATE_SOURCE_COLUMNS = [
    "상품코드",
    "매출일자",
    "바코드",
    "지점",
    "카테고리",
    "상품명",
    "수량",
    "총매출액(원)",
    "할인액(원)",
    "실매출액",
    "가액(원)",
    "부가세(원)",
]


class PosLoaderError(Exception):
    pass


@dataclass
class HeaderDetectionResult:
    source_system: str
    sheet_name: str
    header_row_index: int
    matched_columns: list[str]
    raw_frame: pd.DataFrame


@dataclass
class TransformationResult:
    source_system: str
    source_frame: pd.DataFrame
    transformed_frame: pd.DataFrame
    detection: HeaderDetectionResult
    staging_frame: pd.DataFrame | None = None


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


def detect_source_system(workbook: dict[str, pd.DataFrame]) -> str:
    for _, frame in workbook.items():
        if len(frame.index) > HEADER_ROW_INDEX:
            legacy_row = [normalize_header(cell) for cell in frame.iloc[HEADER_ROW_INDEX].tolist()]
            legacy_matches = set(legacy_row) & set(header_alias_lookup().keys())
            if len(legacy_matches) >= 8:
                return SOURCE_SYSTEM_LEGACY

        if len(frame.index) > 0:
            first_row = [normalize_header(cell) for cell in frame.iloc[0].tolist()]
            if {"상품코드", "매출일자", "지점", "카테고리", "상품명", "총매출액(원)"}.issubset(set(first_row)):
                return SOURCE_SYSTEM_ALTERNATE

    return SOURCE_SYSTEM_LEGACY


def detect_header_row(
    workbook: dict[str, pd.DataFrame],
    source_system: str | None = None,
) -> HeaderDetectionResult:
    source_system = source_system or detect_source_system(workbook)
    best_result: HeaderDetectionResult | None = None
    best_score = -1

    if source_system == SOURCE_SYSTEM_ALTERNATE:
        expected = {normalize_header(column): column for column in ALTERNATE_SOURCE_COLUMNS}
        expected_columns = ALTERNATE_SOURCE_COLUMNS
        preferred_rows = [0]
    else:
        expected = header_alias_lookup()
        expected_columns = CANONICAL_SOURCE_COLUMNS
        preferred_rows = [HEADER_ROW_INDEX]

    for sheet_name, frame in workbook.items():
        candidate_rows = preferred_rows[:]
        candidate_rows.extend(index for index in range(len(frame.index)) if index not in preferred_rows)

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
                    source_system=source_system,
                    sheet_name=sheet_name,
                    header_row_index=row_index,
                    matched_columns=sorted(set(matches), key=expected_columns.index),
                    raw_frame=frame,
                )
            if score == len(expected_columns):
                return best_result

    if best_result is None or best_score <= 0:
        raise PosLoaderError(
            "엑셀에서 헤더 행을 찾지 못했습니다. "
            f"필수 컬럼: {', '.join(expected_columns)}"
        )

    missing = [column for column in expected_columns if column not in best_result.matched_columns]
    raise PosLoaderError(
        "엑셀 헤더는 찾았지만 필수 컬럼이 부족합니다. "
        f"시트='{best_result.sheet_name}', 헤더 행={best_result.header_row_index + 1}, "
        f"누락 컬럼={missing}"
    )


def read_workbook(source: str | Path | BinaryIO) -> dict[str, pd.DataFrame]:
    return pd.read_excel(source, sheet_name=None, header=None, dtype=object)


def read_source_dataframe(source: str | Path | BinaryIO) -> tuple[pd.DataFrame, HeaderDetectionResult]:
    workbook = read_workbook(source)
    source_system = detect_source_system(workbook)
    detection = detect_header_row(workbook, source_system=source_system)
    frame = detection.raw_frame.copy()
    raw_headers = frame.iloc[detection.header_row_index].tolist()
    if detection.source_system == SOURCE_SYSTEM_ALTERNATE:
        headers = [normalize_header(value) for value in raw_headers]
    else:
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


def parse_decimal_value(value: object) -> Decimal | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return Decimal(str(value))
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
    if cleaned in {"", "-"}:
        return None
    amount = Decimal(cleaned)
    return -amount if negative and amount > 0 else amount


def round_decimal_to_int(value: object) -> int | None:
    amount = parse_decimal_value(value)
    if amount is None:
        return None
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


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


def validate_required_columns_alternate(frame: pd.DataFrame) -> None:
    missing = [column for column in ALTERNATE_SOURCE_COLUMNS if column not in frame.columns]
    if missing:
        raise PosLoaderError("대체 POS 원본 컬럼이 누락되었습니다: " + ", ".join(missing))


def normalize_string(value: object) -> str:
    return "" if value is None or pd.isna(value) else str(value).strip()


def normalize_alternate_product_id(raw_product_code: object, barcode: object, store_name: str, sales_date: object, product_name: str) -> int:
    raw_code = normalize_string(raw_product_code)
    barcode_text = normalize_string(barcode)

    for candidate in (raw_code, barcode_text):
        digits = "".join(character for character in candidate if character.isdigit())
        if digits:
            return int(digits)

    surrogate_seed = f"{store_name}|{sales_date}|{product_name}"
    digest = sha1(surrogate_seed.encode("utf-8")).hexdigest()[:12]
    return -int(digest, 16)


def build_alternate_row_key(
    sales_date: object,
    store_name: str,
    raw_product_code: object,
    product_name: str,
    category_mid: str,
) -> str:
    raw_code = normalize_string(raw_product_code) or "uncoded"
    key_seed = f"{sales_date}|{store_name}|{raw_code}|{product_name}|{category_mid}"
    return f"altpos::{sha1(key_seed.encode('utf-8')).hexdigest()[:20]}"


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


def remove_total_rows_alternate(frame: pd.DataFrame) -> pd.DataFrame:
    working = frame.copy().dropna(how="all")
    product_code = working["상품코드"].map(normalize_string)
    return working.loc[~product_code.isin(TOTAL_ROW_MARKERS)].reset_index(drop=True)


def build_alternate_staging_dataframe(frame: pd.DataFrame) -> pd.DataFrame:
    validate_required_columns_alternate(frame)
    cleaned = remove_total_rows_alternate(frame).copy()
    staging = pd.DataFrame()
    staging["sales_date"] = cleaned["매출일자"].map(parse_date_value)
    staging["raw_product_code"] = cleaned["상품코드"].map(normalize_string)
    staging["barcode"] = cleaned["바코드"].map(normalize_string)
    staging["store_name"] = cleaned["지점"].map(normalize_string)
    staging["raw_category"] = cleaned["카테고리"].map(normalize_string)
    staging["product_name"] = cleaned["상품명"].map(normalize_string)
    staging["quantity"] = cleaned["수량"].map(parse_int_value)
    staging["gross_sales_raw"] = cleaned["총매출액(원)"].map(parse_decimal_value)
    staging["discount_amount_raw"] = cleaned["할인액(원)"].map(parse_decimal_value)
    staging["net_sales_raw"] = cleaned["실매출액"].map(parse_decimal_value)
    staging["supply_amount_raw"] = cleaned["가액(원)"].map(parse_decimal_value)
    staging["vat_amount_raw"] = cleaned["부가세(원)"].map(parse_decimal_value)
    staging["source_system"] = SOURCE_SYSTEM_ALTERNATE
    staging["canonical_product_id"] = [
        normalize_alternate_product_id(raw_code, barcode, store_name, sales_date, product_name)
        for raw_code, barcode, store_name, sales_date, product_name in zip(
            cleaned["상품코드"],
            cleaned["바코드"],
            staging["store_name"],
            staging["sales_date"],
            staging["product_name"],
        )
    ]
    staging["generated_row_key"] = [
        build_alternate_row_key(sales_date, store_name, raw_code, product_name, category)
        for sales_date, store_name, raw_code, product_name, category in zip(
            staging["sales_date"],
            staging["store_name"],
            cleaned["상품코드"],
            staging["product_name"],
            staging["raw_category"],
        )
    ]
    return staging


def transform_alternate_dataframe(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    staging = build_alternate_staging_dataframe(frame)
    transformed = pd.DataFrame()
    transformed["row_key"] = staging["generated_row_key"].astype("string")
    transformed["sales_date"] = staging["sales_date"]
    transformed["product_id"] = staging["canonical_product_id"].map(str)
    transformed["store_name"] = staging["store_name"].astype("string")
    transformed["category_mid"] = staging["raw_category"].astype("string")
    transformed["product_name"] = staging["product_name"].astype("string")
    transformed["quantity"] = staging["quantity"].astype("Int64")
    transformed["gross_sales"] = staging["gross_sales_raw"].map(round_decimal_to_int).astype("Int64")
    transformed["discount_amount"] = staging["discount_amount_raw"].map(round_decimal_to_int).astype("Int64")
    transformed["net_sales"] = staging["net_sales_raw"].map(round_decimal_to_int).astype("Int64")
    transformed["supply_amount"] = staging["supply_amount_raw"].map(round_decimal_to_int).astype("Int64")
    transformed["vat_amount"] = staging["vat_amount_raw"].map(round_decimal_to_int).astype("Int64")
    transformed = transformed[TARGET_COLUMNS].reset_index(drop=True)
    return transformed, staging


def transform_uploaded_source(source: str | Path | BinaryIO) -> TransformationResult:
    source_frame, detection = read_source_dataframe(source)
    if detection.source_system == SOURCE_SYSTEM_ALTERNATE:
        transformed, staging = transform_alternate_dataframe(source_frame)
        return TransformationResult(
            source_system=detection.source_system,
            source_frame=source_frame,
            transformed_frame=transformed,
            detection=detection,
            staging_frame=staging,
        )

    transformed = transform_dataframe(source_frame)
    return TransformationResult(
        source_system=detection.source_system,
        source_frame=source_frame,
        transformed_frame=transformed,
        detection=detection,
        staging_frame=None,
    )


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
    print(f"[source] detected_source_system={detection.source_system}")
    print(f"[source] detected_sheet={detection.sheet_name}")
    print(f"[source] detected_header_row={detection.header_row_index + 1}")
    print(f"[source] matched_columns={', '.join(detection.matched_columns)}")
    print(f"[result] transformed_rows={len(dataframe)}")
    print(f"[result] columns={TARGET_COLUMNS}")
    if not dataframe.empty:
        print("[preview]")
        print(dataframe.head(10).to_string(index=False))
