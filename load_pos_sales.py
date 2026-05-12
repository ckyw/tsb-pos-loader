from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from bigquery_loader import TARGET_DEFAULT_POS, TARGET_SD_POS, load_to_bigquery
from etl import (
    DEFAULT_POS_DIR,
    PosLoaderError,
    print_dry_run_summary,
    resolve_input_file,
    SOURCE_SYSTEM_ALTERNATE,
    SOURCE_SYSTEM_LEGACY,
    transform_uploaded_source,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load Tapshopbar POS Excel files into an existing BigQuery table."
    )
    parser.add_argument("--file", help="Path to the POS .xlsx/.xls file")
    parser.add_argument(
        "--date",
        help="POS file date suffix used with pos/tsd{date}.xlsx or pos/tsd{date}.xls",
    )
    parser.add_argument(
        "--pos-dir",
        default=DEFAULT_POS_DIR,
        help="Directory that stores daily POS files",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Transform and preview rows without loading to BigQuery")
    mode.add_argument("--load", action="store_true", help="Load transformed rows into BigQuery")
    parser.add_argument(
        "--duplicate-strategy",
        choices=("skip", "replace"),
        default="skip",
        help="How to handle row_keys that already exist in BigQuery",
    )
    parser.add_argument(
        "--target",
        choices=(TARGET_DEFAULT_POS, TARGET_SD_POS),
        default=TARGET_DEFAULT_POS,
        help="BigQuery target to load into",
    )
    return parser


def main() -> None:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()

    if not args.dry_run and not args.load:
        parser.error("하나의 실행 모드를 선택하세요: --dry-run 또는 --load")

    try:
        file_path = resolve_input_file(args.file, args.date, args.pos_dir)
        if not file_path.exists():
            raise SystemExit(f"입력 파일을 찾을 수 없습니다: {file_path}")

        result = transform_uploaded_source(file_path)
        transformed = result.transformed_frame
        print_dry_run_summary(transformed, result.detection, file_path)
        if result.staging_frame is not None:
            print(f"[source] staging_rows={len(result.staging_frame)}")
            print("[source] alternate_pos_assumption=rounded monetary fields and generated row_key/product_id mapping applied")

        if args.target == TARGET_DEFAULT_POS and result.source_system != SOURCE_SYSTEM_LEGACY:
            raise PosLoaderError("기존 POS 타깃에는 기존 POS 포맷 파일만 적재할 수 있습니다.")
        if args.target == TARGET_SD_POS and result.source_system != SOURCE_SYSTEM_ALTERNATE:
            raise PosLoaderError("SD POS 타깃에는 SD POS 포맷 파일만 적재할 수 있습니다.")

        if args.load:
            loaded_count, duplicate_count, deleted_count = load_to_bigquery(
                transformed,
                duplicate_strategy=args.duplicate_strategy,
                target_key=args.target,
            )
            print(f"[bigquery] target={args.target}")
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


if __name__ == "__main__":
    main()
