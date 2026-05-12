from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd
from google.cloud.bigquery import SchemaField
from google.cloud.bigquery.table import Table

from bigquery_loader import coerce_dataframe_to_table_schema
from etl import (
    HEADER_ROW_INDEX,
    SOURCE_SYSTEM_ALTERNATE,
    TARGET_COLUMNS,
    detect_header_row,
    parse_int_value,
    read_source_dataframe,
    resolve_input_file,
    transform_alternate_dataframe,
    transform_uploaded_source,
    transform_dataframe,
)


class PosSalesLoaderTests(unittest.TestCase):
    def test_parse_int_value_handles_currency_strings(self) -> None:
        self.assertEqual(parse_int_value("1,234원"), 1234)
        self.assertEqual(parse_int_value("₩ 5,678"), 5678)
        self.assertEqual(parse_int_value("(9,000원)"), -9000)
        self.assertIsNone(parse_int_value(""))

    def test_detect_header_row_finds_offset_header(self) -> None:
        workbook = {
            "Sheet1": pd.DataFrame(
                [
                    ["탭샵바 POS", None, None],
                    [None, None, None],
                    ["매장코드_매출일자_상품코드", "매출일자", "상품코드", "지점", "중분류", "상품명", "수량", "총매출액", "할인액", "실매출액", "가액", "부가세"],
                    ["001_20260428_ABC", "2026-04-28", "ABC", "성수", "와인", "레드", 1, 10000, 0, 10000, 9091, 909],
                ]
            )
        }
        result = detect_header_row(workbook)
        self.assertEqual(result.sheet_name, "Sheet1")
        self.assertEqual(result.header_row_index, 2)

    def test_transform_dataframe_removes_total_rows_and_orders_columns(self) -> None:
        source = pd.DataFrame(
            [
                {
                    "매장코드_매출일자_상품코드": "001_20260428_ABC",
                    "매출일자": "2026-04-28",
                    "상품코드": "ABC",
                    "store_name": "성수점",
                    "중분류": "와인",
                    "상품명": "하우스와인",
                    "수량": "2",
                    "총매출액": "20,000원",
                    "할인액": "1,000원",
                    "실매출액": "19,000원",
                    "가액": "17,273원",
                    "부가세": "1,727원",
                },
                {
                    "매장코드_매출일자_상품코드": "",
                    "매출일자": "",
                    "상품코드": "",
                    "store_name": "합계",
                    "중분류": "",
                    "상품명": "",
                    "수량": "2",
                    "총매출액": "20,000원",
                    "할인액": "1,000원",
                    "실매출액": "19,000원",
                    "가액": "17,273원",
                    "부가세": "1,727원",
                },
            ]
        )
        transformed = transform_dataframe(source)
        self.assertEqual(list(transformed.columns), TARGET_COLUMNS)
        self.assertEqual(len(transformed), 1)
        self.assertEqual(transformed.loc[0, "gross_sales"], 20000)
        self.assertEqual(str(transformed.loc[0, "sales_date"]), "2026-04-28")

    def test_read_source_dataframe_uses_detected_header_row(self) -> None:
        sheet = pd.DataFrame(
            [
                ["리포트", None, None],
                ["생성일", "2026-04-28", None],
                ["매장코드_매출일자_상품코드", "매출일자", "상품코드", "대분류", "중분류", "상품명", "수량", "총매출액", "할인액", "실매출액", "가액", "부가세"],
                ["001_20260428_ABC", "2026-04-28", "ABC", "성수점", "와인", "하우스와인", 1, "10,000원", "0원", "10,000원", "9,091원", "909원"],
            ]
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "sample.xlsx"
            with pd.ExcelWriter(path, engine="openpyxl") as writer:
                sheet.to_excel(writer, index=False, header=False, sheet_name="매출데이터")

            source_frame, detection = read_source_dataframe(path)
            self.assertEqual(detection.sheet_name, "매출데이터")
            self.assertEqual(detection.header_row_index, 2)
            self.assertEqual(source_frame.iloc[0]["상품코드"], "ABC")
            self.assertEqual(source_frame.iloc[0]["store_name"], "성수점")

    def test_detect_header_row_prefers_sixth_row(self) -> None:
        rows = [[None] * 12 for _ in range(8)]
        rows[HEADER_ROW_INDEX] = [
            "매장코드_매출일자_상품코드",
            "매출일자",
            "상품코드",
            "대분류",
            "중분류",
            "상품명",
            "수량",
            "총매출액",
            "할인액",
            "실매출액",
            "가액",
            "부가세",
        ]
        workbook = {"Sheet1": pd.DataFrame(rows)}
        result = detect_header_row(workbook)
        self.assertEqual(result.header_row_index, HEADER_ROW_INDEX)

    def test_resolve_input_file_prefers_pos_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            pos_dir = Path(tmp_dir) / "pos"
            pos_dir.mkdir()
            sample = pos_dir / "tsd0425.xlsx"
            sample.write_text("x")
            resolved = resolve_input_file(None, "0425", str(pos_dir))
            self.assertEqual(resolved, sample)

    def test_coerce_dataframe_to_table_schema_uses_bigquery_integer_type(self) -> None:
        dataframe = pd.DataFrame(
            [
                {
                    "row_key": "W03552_2026-04-25_900002",
                    "sales_date": "2026-04-25",
                    "product_id": "900002",
                    "store_name": "청계천 삼일빌딩점",
                    "category_mid": "카페",
                    "product_name": "아메리카노(H)",
                    "quantity": "1",
                    "gross_sales": "3,800원",
                    "discount_amount": "0원",
                    "net_sales": "3,800원",
                    "supply_amount": "3,455원",
                    "vat_amount": "345원",
                }
            ]
        )
        table = Table("okpos-sales-load.okpos_sales.okpos_0301_partitioned")
        table.schema = [
            SchemaField("row_key", "STRING"),
            SchemaField("sales_date", "DATE"),
            SchemaField("product_id", "INTEGER"),
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
        coerced = coerce_dataframe_to_table_schema(dataframe, table)
        self.assertEqual(int(coerced.loc[0, "product_id"]), 900002)
        self.assertEqual(int(coerced.loc[0, "gross_sales"]), 3800)
        self.assertEqual(str(coerced["product_id"].dtype), "int64")

    def test_detect_alternate_source_header_on_first_row(self) -> None:
        workbook = {
            "sales_by_product": pd.DataFrame(
                [
                    ["상품코드", "매출일자", "바코드", "지점", "카테고리", "상품명", "수량", "총매출액(원)", "할인액(원)", "실매출액", "가액(원)", "부가세(원)"],
                    ["P_0007", "2026-04-01", "", "탭샵바 상암mbc점", "COFFEE", "아메리카노", "2", "9800.00", "0.00", "9800.00", "8909.09", "890.91"],
                ]
            )
        }
        result = detect_header_row(workbook, source_system=SOURCE_SYSTEM_ALTERNATE)
        self.assertEqual(result.header_row_index, 0)
        self.assertEqual(result.source_system, SOURCE_SYSTEM_ALTERNATE)

    def test_transform_alternate_dataframe_generates_row_key_and_numeric_product_id(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "상품코드": "P_000000000000000007",
                    "매출일자": "2026-04-01",
                    "바코드": "",
                    "지점": "탭샵바 상암mbc점",
                    "카테고리": "ICE CREAM & COFFEE",
                    "상품명": "아메리카노",
                    "수량": "13",
                    "총매출액(원)": "54600.00",
                    "할인액(원)": "0.00",
                    "실매출액": "54600.00",
                    "가액(원)": "49636.36",
                    "부가세(원)": "4963.64",
                },
                {
                    "상품코드": "합계",
                    "매출일자": "",
                    "바코드": "",
                    "지점": "",
                    "카테고리": "",
                    "상품명": "",
                    "수량": "13",
                    "총매출액(원)": "54600.00",
                    "할인액(원)": "0.00",
                    "실매출액": "54600.00",
                    "가액(원)": "49636.36",
                    "부가세(원)": "4963.64",
                },
            ]
        )
        transformed, staging = transform_alternate_dataframe(frame)
        self.assertEqual(len(transformed), 1)
        self.assertTrue(str(transformed.loc[0, "row_key"]).startswith("altpos::"))
        self.assertEqual(transformed.loc[0, "product_id"], "7")
        self.assertEqual(int(transformed.loc[0, "supply_amount"]), 49636)
        self.assertEqual(int(transformed.loc[0, "vat_amount"]), 4964)
        self.assertEqual(int(staging.loc[0, "canonical_product_id"]), 7)

    def test_transform_uploaded_source_returns_staging_for_alternate_pos(self) -> None:
        sheet = pd.DataFrame(
            [
                ["상품코드", "매출일자", "바코드", "지점", "카테고리", "상품명", "수량", "총매출액(원)", "할인액(원)", "실매출액", "가액(원)", "부가세(원)"],
                ["P_0007", "2026-04-01", "", "탭샵바 상암mbc점", "COFFEE", "아메리카노", "2", "9800.00", "0.00", "9800.00", "8909.09", "890.91"],
            ]
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "alternate.xlsx"
            with pd.ExcelWriter(path, engine="openpyxl") as writer:
                sheet.to_excel(writer, index=False, header=False, sheet_name="sales_by_product")
            result = transform_uploaded_source(path)
            self.assertEqual(result.source_system, SOURCE_SYSTEM_ALTERNATE)
            self.assertIsNotNone(result.staging_frame)
            self.assertEqual(len(result.transformed_frame), 1)


if __name__ == "__main__":
    unittest.main()
