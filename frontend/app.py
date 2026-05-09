"""Streamlit main entry point — redirects to dashboard."""

import streamlit as st

st.set_page_config(
    page_title="AI闪购运营中台",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

pg = st.navigation([
    st.Page("pages/dashboard.py", title="数据看板", icon="📊"),
    st.Page("pages/workflow.py", title="工作流操作台", icon="⚙️"),
    st.Page("pages/diagnosis.py", title="诊断面板", icon="🔍"),
    st.Page("pages/postmortem.py", title="活动复盘", icon="📋"),
    st.Page("pages/ab_experiment.py", title="AB 实验", icon="🧪"),
    st.Page("pages/replay.py", title="执行回放", icon="⏪"),
])
pg.run()
