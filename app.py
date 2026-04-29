from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

from bigquery_loader import count_existing_row_keys, load_to_bigquery
from etl import PosLoaderError, read_source_dataframe, transform_dataframe, validate_required_columns


st.set_page_config(page_title="POS BigQuery Loader", page_icon=":bar_chart:", layout="wide")
st.title("Tapshopbar POS BigQuery Loader")
st.caption("엑셀 업로드, 변환 미리보기, 중복 확인, BigQuery 적재를 브라우저에서 처리합니다.")

uploaded_file = st.file_uploader(
    "POS 엑셀 파일 업로드",
    type=["xlsx", "xls"],
    help="일일 POS 엑셀 파일을 업로드하세요.",
)

duplicate_strategy = st.selectbox(
    "중복 처리 방식",
    options=["skip", "replace"],
    index=0,
    help="기본값은 dry-run이며, 아래 버튼을 누를 때만 실제 BigQuery 적재가 수행됩니다.",
)

if uploaded_file is None:
    st.info("파일을 업로드하면 변환 결과와 BigQuery 중복 정보를 확인할 수 있습니다.")
    st.stop()

try:
    suffix = Path(uploaded_file.name).suffix or ".xlsx"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
        temp_file.write(uploaded_file.getbuffer())
        temp_path = Path(temp_file.name)

    try:
        source_frame, detection = read_source_dataframe(temp_path)
        validate_required_columns(source_frame)
        dataframe = transform_dataframe(source_frame)
    finally:
        temp_path.unlink(missing_ok=True)

    st.success("파일 분석과 변환이 완료되었습니다. 현재 상태는 dry-run입니다.")

    col1, col2, col3 = st.columns(3)
    col1.metric("감지된 시트", detection.sheet_name)
    col2.metric("감지된 헤더 행", str(detection.header_row_index + 1))
    col3.metric("변환된 행 수", str(len(dataframe)))

    st.subheader("필수 컬럼 검증")
    st.success("필수 컬럼이 모두 확인되었습니다.")
    st.write(", ".join(detection.matched_columns))

    st.subheader("변환 결과 미리보기")
    st.dataframe(dataframe, use_container_width=True, hide_index=True)

    st.subheader("BigQuery 중복 확인")
    duplicate_count = count_existing_row_keys(dataframe)
    st.info(f"기존 BigQuery와 row_key를 비교한 결과 중복 {duplicate_count}건입니다.")

    if st.button("BigQuery에 적재", type="primary"):
        loaded_count, existing_count, deleted_count = load_to_bigquery(
            dataframe,
            duplicate_strategy=duplicate_strategy,
        )
        st.success(f"BigQuery 적재가 완료되었습니다. 적재 행 수: {loaded_count}")
        st.write(f"중복 row_key 수: {existing_count}")
        if duplicate_strategy == "replace":
            st.write(f"삭제 후 재적재한 기존 row 수: {deleted_count}")
except PosLoaderError as exc:
    st.error(f"변환 또는 검증 중 문제가 발생했습니다: {exc}")
except Exception as exc:
    st.error(f"BigQuery 처리 중 오류가 발생했습니다: {exc}")
