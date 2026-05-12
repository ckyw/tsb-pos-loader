# tapshopbar-pos-loader

탭샵바 POS 데일리 매출 엑셀 파일을 로컬 Python CLI 또는 Streamlit UI로 읽어서, 기존 BigQuery 테이블에 안전하게 append 적재하는 프로젝트입니다. 현재는 기존 POS 포맷과 별도 지점의 대체 POS 포맷을 모두 감지할 수 있습니다.

실제 적재는 `--load`일 때만 수행하며, 기본 동작은 `row_key` 기준 중복을 건너뛰는 `skip` 전략입니다.

## 1. 프로젝트 목적

- POS `.xlsx` 파일을 읽어 기존 BigQuery 스키마에 맞는 DataFrame으로 변환
- 빈 행, 합계 행 제거
- 한글 원본 컬럼을 영문 BigQuery 컬럼으로 매핑
- 포맷별 source adapter로 다른 POS export도 처리
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

주의:

- `GCP_PROJECT_ID`에는 프로젝트 ID만 넣습니다. 예: `okpos-sales-load`
- `BIGQUERY_DATASET`에는 dataset 이름만 넣습니다. 예: `okpos_sales`
- `BIGQUERY_TABLE`에는 table 이름만 넣습니다. 예: `okpos_0301_partitioned`
- `okpos-sales-load.okpos_sales.okpos_0301_partitioned`처럼 전체 경로를 `BIGQUERY_TABLE`에 넣으면 안 됩니다.

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

### CLI

권장 구조:

```text
pos/
└── tsd0425.xlsx
```

날짜 suffix 기반 실행:

```bash
python load_pos_sales.py \
  --date 0425 \
  --dry-run
```

명시적 파일 경로로도 실행할 수 있습니다.

```bash
python load_pos_sales.py \
  --file pos/tsd0425.xlsx \
  --dry-run
```

출력 내용:

- 감지된 시트명
- 감지된 헤더 행 번호
- 최종 컬럼 목록
- 변환된 행 수
- 상위 10개 preview

### Streamlit UI

```bash
streamlit run app.py
```

브라우저 UI에서 할 수 있는 일:

- POS 엑셀 파일 업로드
- 기존 POS / 대체 POS 포맷 자동 감지
- 변환된 DataFrame 미리보기
- 필수 컬럼 검증 결과 확인
- 기존 BigQuery `row_key` 중복 개수 확인
- 중복 처리 방식 선택
- 버튼 클릭 시에만 실제 BigQuery 적재

## 6. 실제 적재 실행 방법

```bash
python load_pos_sales.py \
  --date 0425 \
  --load
```

BigQuery 적재는 `google-cloud-bigquery`의 `load_table_from_dataframe()`와 `WRITE_APPEND`를 사용합니다.

참고:

- `load_table_from_dataframe()` 경로에서 pandas/pyarrow 변환 오류가 발생하면 내부적으로 CSV 기반 fallback 적재를 수행합니다.
- Streamlit UI는 기본적으로 dry-run 상태이며, `BigQuery에 적재` 버튼을 눌렀을 때만 실제 적재합니다.
- 대체 POS 포맷은 `row_key` 생성, `product_id` 숫자화, `가액/부가세` 반올림 정수화 규칙을 사용합니다.

## 7. 중복 처리 옵션 설명

기본값은 `skip`입니다.

```bash
python load_pos_sales.py \
  --date 0425 \
  --load \
  --duplicate-strategy skip
```

- `skip`: 이미 BigQuery에 있는 `row_key`는 적재하지 않고 신규 `row_key`만 append

```bash
python load_pos_sales.py \
  --date 0425 \
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
- POS 원본 규격상 1~5행은 버리고 6행이 헤더인지
- `sales_date`가 날짜로 변환 가능한 값인지
- 기존 BigQuery 테이블의 컬럼 이름, 순서, 타입이 아래 목록과 정확히 같은지
- 실제 운영 테이블 타입이 문서와 다를 수 있으므로, 현재 로더는 BigQuery 실테이블 스키마를 기준으로 최종 타입을 다시 맞춥니다. 예를 들어 `product_id`가 테이블에서 `INTEGER`면 적재 직전에 숫자형으로 변환합니다.

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
│   ├── alternate_pos_integration.md
│   └── load_pos_sales.md
├── execution/
│   └── pos_sales_loader.py
├── samples/
├── tests/
│   └── test_pos_sales_loader.py
├── .env.example
├── app.py
├── bigquery_loader.py
├── etl.py
├── load_pos_sales.py
├── README.md
└── requirements.txt
```

## 참고

- 이번 구현은 기존 BigQuery 테이블 스키마를 변경하지 않습니다.
- `loaded_at`, `source_file_name`은 적재하지 않습니다.
- 샘플 엑셀 파일은 현재 저장소에 포함되어 있지 않으므로 `samples/sample.xlsx`는 사용자가 배치해야 합니다.
- `google-cloud-bigquery` 최신 지원 범위를 고려해 Python 3.11 환경을 권장합니다.
- 현재 확인한 POS 샘플 규격은 `.xls` 또는 `.xlsx` 모두 허용하며, 기본적으로 `pos/tsd{date}.xlsx` 또는 `pos/tsd{date}.xls`를 찾습니다.
- 대체 POS 통합 규칙과 staging 스키마 제안은 [directives/alternate_pos_integration.md](/Users/ckp/vibecoding/load-pos-sales/directives/alternate_pos_integration.md)에 정리되어 있습니다.
