from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from card_sales_etl import (
    CARD_TARGET_COLUMNS,
    OKPOS_CARD_SOURCE_SYSTEM,
    build_okpos_card_row_key,
    transform_okpos_card_dataframe,
    transform_okpos_card_source,
)


class CardSalesLoaderTests(unittest.TestCase):
    def test_transform_okpos_card_dataframe_excludes_summary_rows(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "일자": "2026-08-01",
                    "매장명": "탭샵바 여의도점",
                    "총매출": "5,007,200",
                    "실매출": "4,438,130",
                    "결제합계": "4,438,130",
                    "신용카드": "4,408,130",
                },
                {
                    "일자": "소계:2026-08-01",
                    "매장명": "4개",
                    "총매출": "15,908,100",
                    "실매출": "14,628,290",
                    "결제합계": "14,628,290",
                    "신용카드": "14,466,890",
                },
                {
                    "일자": "합계",
                    "매장명": None,
                    "총매출": "15,908,100",
                    "실매출": "14,628,290",
                    "결제합계": "14,628,290",
                    "신용카드": "14,466,890",
                },
            ]
        )
        loaded_at = datetime(2026, 8, 2, tzinfo=timezone.utc)
        transformed = transform_okpos_card_dataframe(frame, "card.xls", loaded_at=loaded_at)

        self.assertEqual(list(transformed.columns), CARD_TARGET_COLUMNS)
        self.assertEqual(len(transformed), 1)
        self.assertEqual(str(transformed.loc[0, "sales_date"]), "2026-08-01")
        self.assertEqual(int(transformed.loc[0, "card_sales_amount"]), 4_408_130)
        self.assertEqual(transformed.loc[0, "source_system"], OKPOS_CARD_SOURCE_SYSTEM)
        self.assertEqual(
            transformed.loc[0, "row_key"],
            build_okpos_card_row_key(pd.Timestamp("2026-08-01").date(), "탭샵바 여의도점"),
        )

    def test_transform_okpos_card_source_detects_seventh_row_header(self) -> None:
        rows = [[None] * 6 for _ in range(6)]
        rows.append(["일자", "매장명", "총매출", "실매출", "결제합계", "신용카드"])
        rows.append(["2026-08-01", "탭샵바 합정점", 4_293_600, 4_021_820, 4_021_820, 3_936_620])
        rows.append(["소계:2026-08-01", "1개", 4_293_600, 4_021_820, 4_021_820, 3_936_620])

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "okpos-card.xlsx"
            pd.DataFrame(rows).to_excel(path, index=False, header=False)
            result = transform_okpos_card_source(path)

        self.assertEqual(result.detection.header_row_index, 6)
        self.assertEqual(len(result.transformed_frame), 1)
        self.assertEqual(int(result.transformed_frame.loc[0, "card_sales_amount"]), 3_936_620)


if __name__ == "__main__":
    unittest.main()
