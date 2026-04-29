# tapshopbar-pos-loader

탭샵바 POS 데일리 매출 엑셀 파일을 로컬 Python CLI로 읽어서, 기존 BigQuery 테이블에 안전하게 append 적재하는 프로젝트입니다.

실제 적재는 `--load`일 때만 수행하며, 기본 동작은 `row_key` 기준 중복을 건너뛰는 `skip` 전략입니다.

## 1. 프로젝트 목적

- POS `.xlsx` 파일을 읽어 기존 BigQuery 스키마에 맞는 DataFrame으로 변환
- 빈 행, 합계 행 제거
- 한글 원본 컬럼을 영문 BigQuery 컬럼으로 매핑
- `row_key` 기준 중복을 제어하면서 기존 테이블에 append 적재

## 2. 설치 방법

Python 3.11 사용을 권장합니다.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

만약 macOS에 Python 3.11이 없다면 예시:

```bash
brew install python@3.11
```

## 3. `.env` 설정 방법

루트에 `.env` 파일을 만들고 아래 값을 채웁니다.

```bash
cp .env.example .env
```

```env
GCP_PROJECT_ID=your-gcp-project-id
BIGQUERY_DATASET=your_dataset
BIGQUERY_TABLE=your_table
```

## 4. Google Cloud 인증 방법

로컬 PC에서 Application Default Credentials를 사용합니다.

```bash
gcloud auth application-default login
```

필요하다면 프로젝트도 맞춰둡니다.

```bash
gcloud config set project your-gcp-project-id
```

## 5. dry-run 실행 방법

```bash
python load_pos_sales.py \
  --file samples/sample.xlsx \
  --dry-run
```

출력 내용:

- 감지된 시트명
- 감지된 헤더 행 번호
- 최종 컬럼 목록
- 변환된 행 수
- 상위 10개 preview

## 6. 실제 적재 실행 방법

```bash
python load_pos_sales.py \
  --file samples/sample.xlsx \
  --load
```

BigQuery 적재는 `google-cloud-bigquery`의 `load_table_from_dataframe()`와 `WRITE_APPEND`를 사용합니다.

## 7. 중복 처리 옵션 설명

기본값은 `skip`입니다.

```bash
python load_pos_sales.py \
  --file samples/sample.xlsx \
  --load \
  --duplicate-strategy skip
```

- `skip`: 이미 BigQuery에 있는 `row_key`는 적재하지 않고 신규 `row_key`만 append

```bash
python load_pos_sales.py \
  --file samples/sample.xlsx \
  --load \
  --duplicate-strategy replace
```

- `replace`: 파일 안의 `row_key` 중 BigQuery에 이미 있는 값을 먼저 삭제한 뒤, 파일 데이터를 다시 append

## 8. 에러 발생 시 확인할 사항

- `.env`에 `GCP_PROJECT_ID`, `BIGQUERY_DATASET`, `BIGQUERY_TABLE`가 모두 설정되었는지
- `gcloud auth application-default login`이 완료되었는지
- `python --version`이 `3.11.x`인지
- 엑셀에 아래 원본 컬럼이 모두 있는지
- 헤더 행이 병합/이미지/특수 형식 때문에 일반 셀로 읽히는지
- `sales_date`가 날짜로 변환 가능한 값인지
- 기존 BigQuery 테이블의 컬럼 이름, 순서, 타입이 아래 목록과 정확히 같은지

## 9. BigQuery 기존 테이블 컬럼 목록

아래 순서 그대로 사용합니다.

1. `row_key`
2. `sales_date`
3. `product_id`
4. `store_name`
5. `category_mid`
6. `product_name`
7. `quantity`
8. `gross_sales`
9. `discount_amount`
10. `net_sales`
11. `supply_amount`
12. `vat_amount`

## 프로젝트 구조

```text
tapshopbar-pos-loader/
├── directives/
│   └── load_pos_sales.md
├── execution/
│   └── pos_sales_loader.py
├── samples/
├── tests/
│   └── test_pos_sales_loader.py
├── .env.example
├── load_pos_sales.py
├── README.md
└── requirements.txt
```

## 참고

- 이번 구현은 기존 BigQuery 테이블 스키마를 변경하지 않습니다.
- `loaded_at`, `source_file_name`은 적재하지 않습니다.
- 샘플 엑셀 파일은 현재 저장소에 포함되어 있지 않으므로 `samples/sample.xlsx`는 사용자가 배치해야 합니다.
- `google-cloud-bigquery` 최신 지원 범위를 고려해 Python 3.11 환경을 권장합니다.
