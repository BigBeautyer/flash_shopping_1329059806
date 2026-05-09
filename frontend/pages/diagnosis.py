"""Diagnosis panel — anomaly detection + funnel attribution + LLM root cause analysis."""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import httpx
from datetime import datetime

API_BASE = "http://127.0.0.1:8000/api"


# ── State ──

if "diag_report" not in st.session_state:
    st.session_state.diag_report = None
if "diag_district_id" not in st.session_state:
    st.session_state.diag_district_id = 1
if "diag_days" not in st.session_state:
    st.session_state.diag_days = 30


# ── Helpers ──

@st.cache_data(ttl=30)
def fetch_districts():
    try:
        r = httpx.get(f"{API_BASE}/diagnosis/districts", timeout=10)
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []


def run_diagnosis(district_id: int, days: int, use_llm: bool = True) -> dict | None:
    try:
        r = httpx.post(f"{API_BASE}/diagnosis/run", json={
            "district_id": district_id, "days": days, "use_llm": use_llm,
        }, timeout=300)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        st.error(f"诊断失败: {e}")
        return None


def fetch_anomalies(district_id: int, days: int = 7) -> dict | None:
    try:
        r = httpx.get(f"{API_BASE}/diagnosis/anomalies", params={
            "district_id": district_id, "days": days,
        }, timeout=30)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def fetch_reports(limit: int = 20) -> dict | None:
    try:
        r = httpx.get(f"{API_BASE}/diagnosis/reports", params={"limit": limit}, timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


# ── Page ──

st.title("🔍 运营诊断面板")
st.caption("异动检测 → 漏斗分层归因 → LLM 诊断建议")

# ── Sidebar Controls ──

st.sidebar.title("⚡ 诊断控制")

districts = fetch_districts()
district_options = {d.get("name", f"商圈{d['id']}"): d["id"] for d in districts} if districts else {
    "望京": 1, "三里屯": 2, "国贸": 3, "中关村": 4, "五道口": 5,
}
selected_name = st.sidebar.selectbox("选择商圈", list(district_options.keys()))
district_id = district_options[selected_name]
st.session_state.diag_district_id = district_id

days = st.sidebar.slider("诊断周期（天）", 7, 90, 30, 7)
use_llm = st.sidebar.checkbox("使用 LLM 深度分析", value=True,
    help="启用后会用 DeepSeek 生成根因分析和建议。关闭则仅用规则引擎快速诊断")

col_run, col_quick = st.sidebar.columns(2)
with col_run:
    if st.button("🩺 运行诊断", type="primary", use_container_width=True):
        with st.spinner(f"诊断中... 异动检测 → 漏斗归因{' → LLM分析' if use_llm else ''}"):
            report = run_diagnosis(district_id, days, use_llm)
            if report:
                st.session_state.diag_report = report
                st.session_state.diag_days = days
                st.rerun()

with col_quick:
    if st.button("⚡ 快速扫描", use_container_width=True):
        with st.spinner("快速扫描中..."):
            anomalies_data = fetch_anomalies(district_id, days)
            if anomalies_data:
                st.session_state.diag_report = anomalies_data
                st.session_state.diag_days = days
                st.rerun()

# ── Report History ──

with st.sidebar.expander("📋 历史报告", expanded=False):
    reports_data = fetch_reports(10)
    if reports_data and reports_data.get("reports"):
        for r in reports_data["reports"][:10]:
            sev_emoji = {"severe": "🔴", "critical": "🟠", "warning": "🟡", "info": "🟢"}.get(r.get("risk_level", ""), "⚪")
            if st.button(f"{sev_emoji} {r.get('report_id', '')[:30]}...", key=f"hist_{r.get('report_id')}"):
                try:
                    detail = httpx.get(f"{API_BASE}/diagnosis/reports/{r['report_id']}", timeout=10).json()
                    st.session_state.diag_report = detail
                    st.rerun()
                except Exception:
                    pass
    else:
        st.caption("暂无历史报告")

st.sidebar.divider()
st.sidebar.caption("Phase 2 · 诊断 Agent · 规则引擎 + LLM 双层")

# ═══════════ Main Content ═══════════

report = st.session_state.diag_report

if not report:
    st.info("👈 请在左侧选择商圈并点击「运行诊断」或「快速扫描」开始诊断")
    st.stop()

# ── Overall Status ──

sev_config = {
    "severe": ("🔴", "严重", "#dc3545"),
    "critical": ("🟠", "高危", "#fd7e14"),
    "warning": ("🟡", "警告", "#ffc107"),
    "info": ("🟢", "正常", "#28a745"),
}

risk_level = report.get("risk_level", "info")
sev_emoji, sev_label, sev_color = sev_config.get(risk_level, sev_config["info"])

st.markdown(f"### {sev_emoji} 整体状态: {sev_label}")

col1, col2, col3, col4 = st.columns(4)
anomalies = report.get("anomalies", [])
attribution = report.get("attribution", {})

attr_count = len(attribution.get("attributions", [])) if isinstance(attribution, dict) else (len(attribution) if isinstance(attribution, list) else 0)
col1.metric("异常指标数", len(anomalies))
col2.metric("归因匹配", attr_count)
col3.metric("诊断周期", f"{report.get('period_days', st.session_state.diag_days)}天")
col4.metric("置信度", f"{report.get('confidence', 0):.0%}")

# Diagnosis summary from LLM
result = report.get("result", {})
if isinstance(result, dict) and result.get("diagnosis_summary"):
    with st.container(border=True):
        st.markdown(f"**📝 诊断摘要:** {result['diagnosis_summary']}")

st.divider()

# ═══════════ Anomaly Cards ═══════════

if anomalies:
    st.subheader(f"🚨 异动指标 ({len(anomalies)})")

    # Group by severity
    by_severity = {"severe": [], "critical": [], "warning": [], "info": []}
    for a in anomalies:
        sev = a.get("severity", "info")
        by_severity[sev].append(a)

    # Render each severity group
    for sev, sev_anomalies in by_severity.items():
        if not sev_anomalies:
            continue
        emoji, label, color = sev_config.get(sev, sev_config["info"])
        with st.expander(f"{emoji} {label} ({len(sev_anomalies)} 项)", expanded=(sev in ("severe", "critical"))):
            cols = st.columns(min(len(sev_anomalies), 3))
            for i, a in enumerate(sev_anomalies):
                with cols[i % 3]:
                    direction_arrow = "↑" if a.get("direction") == "up" else "↓"
                    direction_color = "#dc3545" if a.get("direction") == "down" else "#28a745"
                    st.markdown(f"""
                    <div style="border:1px solid {color}; border-radius:8px; padding:12px; margin:4px 0;">
                        <strong>{a.get('metric_name', '?')}</strong> <span style="color:{direction_color}">{direction_arrow}</span><br>
                        <small>当前: {a.get('current_value', 0):,.4f}</small><br>
                        <small>期望: {a.get('expected_value', 0):,.4f}</small><br>
                        <small>偏离: {a.get('sigma', 0):.1f}σ | 方法: {a.get('detection_method', '?')}</small><br>
                        <small>层级: {a.get('funnel_layer', '?')} | 日期: {a.get('detected_at', '?')}</small>
                    </div>
                    """, unsafe_allow_html=True)

st.divider()

# ═══════════ Funnel Attribution ═══════════

attributions = attribution.get("attributions", []) if isinstance(attribution, dict) else attribution
if attributions:
    st.subheader(f"🔬 漏斗归因 ({len(attributions)} 条)")

    # Group by funnel layer
    by_layer = {}
    for attr in attributions:
        layer = attr.get("funnel_layer", "unknown")
        if layer not in by_layer:
            by_layer[layer] = []
        by_layer[layer].append(attr)

    funnel_order = ["exposure", "click", "cart", "payment", "repurchase", "unknown"]
    layer_labels = {"exposure": "曝光层", "click": "点击层", "cart": "加购层", "payment": "支付层", "repurchase": "复购层", "unknown": "其他"}

    for layer in funnel_order:
        if layer not in by_layer:
            continue
        layer_attrs = by_layer[layer]

        with st.expander(f"📊 {layer_labels.get(layer, layer)} ({len(layer_attrs)} 条归因)", expanded=(layer in ("payment", "click"))):
            for attr in layer_attrs:
                sev_emoji, _, sev_color = sev_config.get(attr.get("severity", "info"), sev_config["info"])
                st.markdown(f"**{sev_emoji} {attr.get('root_cause_category', '?')}**")
                st.write(attr.get("description", ""))
                if attr.get("fix_direction"):
                    st.caption(f"💡 修复方向: {attr['fix_direction']}")
                if attr.get("matched_anomalies"):
                    detail_text = " | ".join([
                        f"{m['metric']}: {m['current_value']:,.4f} (期望 {m['expected_value']:,.4f})"
                        for m in attr["matched_anomalies"][:3]
                    ])
                    st.caption(f"📈 {detail_text}")
                st.divider()

st.divider()

# ═══════════ LLM Suggestions ═══════════

if isinstance(result, dict):
    suggestions = result.get("suggestions", [])
    if suggestions:
        st.subheader("💡 改进建议")
        for i, sug in enumerate(suggestions):
            priority = sug.get("priority", i + 1)
            effort_emoji = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(sug.get("effort", ""), "")
            with st.container(border=True):
                st.markdown(f"**{priority}. {sug.get('action', '')}**")
                col_e, col_o = st.columns([1, 3])
                col_e.caption(f"难度: {effort_emoji} {sug.get('effort', '?')}")
                col_o.caption(f"负责人: {sug.get('owner', '?')} | 预期影响: {sug.get('expected_impact', '')}")

    # Risk assessment
    risk_assessment = result.get("risk_assessment", "")
    if risk_assessment:
        with st.expander("⚠️ 风险评估", expanded=False):
            st.write(risk_assessment)

st.divider()

# ── Refresh ──
if st.button("🔄 刷新"):
    st.rerun()
