from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

from bigquery_loader import TARGET_DEFAULT_POS, TARGET_LABELS, TARGET_SD_POS, count_existing_row_keys, load_to_bigquery
from card_bigquery_loader import count_existing_card_row_keys, load_card_sales_to_bigquery
from card_sales_etl import transform_okpos_card_source
from etl import PosLoaderError, SOURCE_SYSTEM_ALTERNATE, SOURCE_SYSTEM_LEGACY, transform_uploaded_source


st.set_page_config(page_title="POS BigQuery Loader", page_icon=":bar_chart:", layout="wide")
st.markdown(
    """
    <style>
    .stTabs [data-baseweb="tab-list"] {
        gap: 0.75rem;
        padding: 0.5rem;
        background: linear-gradient(135deg, #edf4f2 0%, #f7faf8 100%);
        border: 1px solid #d7e4df;
        border-radius: 1rem;
    }

    .stTabs [data-baseweb="tab"] {
        flex: 1 1 0;
        justify-content: center;
        height: 3rem;
        padding: 0 1.5rem;
        background: #ffffff;
        border: 1px solid #c9d8d2;
        border-radius: 0.7rem;
        color: #3b5149;
        font-size: 1rem;
        font-weight: 700;
    }

    .stTabs [data-baseweb="tab"]:hover {
        border-color: #16715b;
        color: #0f5b48;
    }

    .stTabs [aria-selected="true"] {
        background: #126b55;
        border-color: #126b55;
        box-shadow: 0 4px 12px rgba(18, 107, 85, 0.2);
        color: #ffffff;
    }

    .stTabs [data-baseweb="tab-highlight"] {
        display: none;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("Tapshopbar POS BigQuery Loader")
st.caption("엑셀 업로드, 변환 미리보기, 중복 확인, BigQuery 적재를 브라우저에서 처리합니다.")


def render_pos_section(
    title: str,
    target_key: str,
    expected_source_system: str,
    uploader_help: str,
) -> None:
    st.subheader(title)
    st.caption(f"적재 대상 테이블: `{TARGET_LABELS[target_key]}`")

    uploaded_file = st.file_uploader(
        f"{title} 엑셀 파일 업로드",
        type=["xlsx", "xls"],
        help=uploader_help,
        key=f"uploader_{target_key}",
    )

    duplicate_strategy = st.selectbox(
        f"{title} 중복 처리 방식",
        options=["skip", "replace"],
        index=0,
        help="기본값은 dry-run이며, 아래 버튼을 누를 때만 실제 BigQuery 적재가 수행됩니다.",
        key=f"dup_{target_key}",
    )

    if uploaded_file is None:
        st.info("파일을 업로드하면 변환 결과와 BigQuery 중복 정보를 확인할 수 있습니다.")
        return

    try:
        suffix = Path(uploaded_file.name).suffix or ".xlsx"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
            temp_file.write(uploaded_file.getbuffer())
            temp_path = Path(temp_file.name)

        try:
            result = transform_uploaded_source(temp_path)
            detection = result.detection
            dataframe = result.transformed_frame
        finally:
            temp_path.unlink(missing_ok=True)

        if detection.source_system != expected_source_system:
            if target_key == TARGET_DEFAULT_POS:
                raise PosLoaderError("OK POS 영역에는 기존 POS 포맷 파일만 업로드할 수 있습니다.")
            raise PosLoaderError("SD POS 영역에는 store_sales_by_product 형식 파일만 업로드할 수 있습니다.")

        st.success("파일 분석과 변환이 완료되었습니다. 현재 상태는 dry-run입니다.")

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("감지된 시트", detection.sheet_name)
        col2.metric("감지된 헤더 행", str(detection.header_row_index + 1))
        col3.metric("변환된 행 수", str(len(dataframe)))
        col4.metric("소스 시스템", detection.source_system)

        st.subheader("필수 컬럼 검증")
        st.success("필수 컬럼이 모두 확인되었습니다.")
        st.write(", ".join(detection.matched_columns))

        if detection.source_system == SOURCE_SYSTEM_ALTERNATE:
            st.warning(
                "SD POS 포맷으로 감지되었습니다. "
                "row_key는 생성 규칙을 사용하고, product_id는 상품코드/바코드 기반 숫자화 또는 surrogate id를 사용합니다. "
                "가액/부가세는 반올림 정수로 적재됩니다."
            )
            if result.staging_frame is not None:
                st.subheader("Staging 미리보기")
                st.dataframe(result.staging_frame, use_container_width=True, hide_index=True)

        st.subheader("변환 결과 미리보기")
        st.dataframe(dataframe, use_container_width=True, hide_index=True)

        st.subheader("BigQuery 중복 확인")
        duplicate_count = count_existing_row_keys(dataframe, target_key=target_key)
        st.info(f"대상 BigQuery 테이블과 row_key를 비교한 결과 중복 {duplicate_count}건입니다.")

        if st.button(f"{title} BigQuery에 적재", type="primary", key=f"load_{target_key}"):
            loaded_count, existing_count, deleted_count = load_to_bigquery(
                dataframe,
                duplicate_strategy=duplicate_strategy,
                target_key=target_key,
            )
            st.success(f"BigQuery 적재가 완료되었습니다. 적재 행 수: {loaded_count}")
            st.write(f"중복 row_key 수: {existing_count}")
            if duplicate_strategy == "replace":
                st.write(f"삭제 후 재적재한 기존 row 수: {deleted_count}")
    except PosLoaderError as exc:
        st.error(f"변환 또는 검증 중 문제가 발생했습니다: {exc}")
    except Exception as exc:
        st.error(f"BigQuery 처리 중 오류가 발생했습니다: {exc}")


def render_okpos_card_section() -> None:
    st.subheader("OK POS 카드매출")
    st.caption("결제수단별 일별종합 파일의 `신용카드` 금액을 카드매출 원천 테이블에 적재합니다.")

    uploaded_file = st.file_uploader(
        "OK POS 카드매출 엑셀 파일 업로드",
        type=["xlsx", "xls"],
        help="OK POS의 결제수단별 (결제수단별-일별종합) 파일을 업로드하세요.",
        key="uploader_okpos_card",
    )
    duplicate_strategy = st.selectbox(
        "OK POS 카드매출 중복 처리 방식",
        options=["skip", "replace"],
        index=0,
        help="같은 매출일·매장의 기존 데이터는 row_key 기준으로 처리합니다.",
        key="dup_okpos_card",
    )

    if uploaded_file is None:
        st.info("파일을 업로드하면 카드매출 변환 결과를 먼저 확인할 수 있습니다.")
        return

    try:
        suffix = Path(uploaded_file.name).suffix or ".xls"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
            temp_file.write(uploaded_file.getbuffer())
            temp_path = Path(temp_file.name)

        try:
            result = transform_okpos_card_source(temp_path, source_file_name=uploaded_file.name)
            dataframe = result.transformed_frame
        finally:
            temp_path.unlink(missing_ok=True)

        st.success("OK POS 카드매출 파일 분석과 변환이 완료되었습니다. 현재 상태는 dry-run입니다.")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("감지된 시트", result.detection.sheet_name)
        col2.metric("감지된 헤더 행", str(result.detection.header_row_index + 1))
        col3.metric("상세 행 수", f"{len(dataframe):,}")
        col4.metric("카드매출 합계", f"{int(dataframe['card_sales_amount'].sum()):,}원")

        st.subheader("변환 결과 미리보기")
        st.dataframe(dataframe, use_container_width=True, hide_index=True)

        bigquery_ready = True
        try:
            duplicate_count = count_existing_card_row_keys(dataframe)
            st.info(f"카드매출 BigQuery 테이블과 비교한 결과 중복 {duplicate_count}건입니다.")
        except Exception as exc:
            bigquery_ready = False
            st.warning(
                "카드매출 BigQuery 테이블이 아직 준비되지 않아 dry-run까지만 가능합니다. "
                f"연결 확인 결과: {exc}"
            )

        if st.button(
            "OK POS 카드매출 BigQuery에 적재",
            type="primary",
            key="load_okpos_card",
            disabled=not bigquery_ready,
        ):
            loaded_count, existing_count, deleted_count = load_card_sales_to_bigquery(
                dataframe,
                duplicate_strategy=duplicate_strategy,
            )
            st.success(f"카드매출 적재가 완료되었습니다. 적재 행 수: {loaded_count}")
            st.write(f"중복 row_key 수: {existing_count}")
            if duplicate_strategy == "replace":
                st.write(f"삭제 후 재적재한 기존 row 수: {deleted_count}")
    except PosLoaderError as exc:
        st.error(f"OK POS 카드매출 변환 중 문제가 발생했습니다: {exc}")
    except Exception as exc:
        st.error(f"OK POS 카드매출 파일 처리 중 오류가 발생했습니다: {exc}")


sd_tab, sd_card_tab, ok_tab, ok_card_tab = st.tabs(
    ["SD POS", "SD POS 카드매출", "OK POS", "OK POS 카드매출"]
)

with sd_tab:
    render_pos_section(
        title="SD POS",
        target_key=TARGET_SD_POS,
        expected_source_system=SOURCE_SYSTEM_ALTERNATE,
        uploader_help="store_sales_by_product 형식의 SD POS 엑셀 파일을 업로드하세요.",
    )

with sd_card_tab:
    st.subheader("SD POS 카드매출")
    st.info("SD POS 카드매출 원천 파일 포맷을 분석한 뒤 이 탭에 연결할 예정입니다.")

with ok_tab:
    render_pos_section(
        title="OK POS",
        target_key=TARGET_DEFAULT_POS,
        expected_source_system=SOURCE_SYSTEM_LEGACY,
        uploader_help="기존 탭샵바 OK POS 엑셀 파일을 업로드하세요.",
    )

with ok_card_tab:
    render_okpos_card_section()
