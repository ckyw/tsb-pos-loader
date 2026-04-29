from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from execution.pos_sales_loader import (
    TARGET_COLUMNS,
    detect_header_row,
    parse_int_value,
    read_source_dataframe,
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
                    "지점": "성수점",
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
                    "지점": "합계",
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
                ["매장코드_매출일자_상품코드", "매출일자", "상품코드", "지점", "중분류", "상품명", "수량", "총매출액", "할인액", "실매출액", "가액", "부가세"],
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


if __name__ == "__main__":
    unittest.main()
