"""PostMortem panel — campaign performance review & lesson extraction."""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import httpx
from datetime import datetime

API_BASE = "http://127.0.0.1:8000/api"

# ── State ──

if "pm_report" not in st.session_state:
    st.session_state.pm_report = None
if "pm_playbooks" not in st.session_state:
    st.session_state.pm_playbooks = None


# ── Helpers ──

def run_postmortem(campaign_id: str, district_id: int, days: int, expected_metrics: dict) -> dict | None:
    try:
        r = httpx.post(f"{API_BASE}/phase3/postmortem/run", json={
            "campaign_id": campaign_id, "district_id": district_id,
            "days": days, "expected_metrics": expected_metrics,
            "operator_feedback": [],
        }, timeout=300)
        if r.status_code == 200:
            return r.json()
        else:
            detail = r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
            st.error(f"复盘失败 (HTTP {r.status_code}): {detail[:300]}")
            return None
    except httpx.TimeoutException:
        st.error("复盘超时：LLM 分析耗时过长（>5分钟）。请确认 DeepSeek API 可用，或减小复盘周期重试。")
        return None
    except Exception as e:
        st.error(f"复盘失败: {e}")
        return None


@st.cache_data(ttl=30)
def fetch_playbooks(limit: int = 20) -> dict | None:
    try:
        r = httpx.get(f"{API_BASE}/phase3/postmortem/playbooks", params={"limit": limit}, timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


# ── Page ──

st.title("📋 活动复盘")
st.caption("Phase 3 · 对比预期 vs 实际 · 提取经验教训 · 沉淀 Playbook")

# ── Sidebar Controls ──

st.sidebar.title("⚡ 复盘控制")

campaign_id = st.sidebar.text_input("活动 ID", value="CAMP_20260507", help="输入要复盘的活动 ID")
district_id = st.sidebar.selectbox("商圈", [1, 2, 3, 4, 5],
    format_func=lambda x: {1: "望京", 2: "三里屯", 3: "国贸", 4: "中关村", 5: "五道口"}[x])
days = st.sidebar.slider("复盘周期（天）", 1, 90, 7, 7)

# Expected metrics input
with st.sidebar.expander("📊 预期指标", expanded=True):
    exp_gmv = st.number_input("预期 GMV", value=5000.0, step=500.0)
    exp_cvr = st.number_input("预期 CVR", value=0.12, step=0.01, format="%.4f")
    exp_ctr = st.number_input("预期 CTR", value=0.10, step=0.01, format="%.4f")
    exp_aov = st.number_input("预期 AOV", value=35.0, step=5.0)
    exp_roi = st.number_input("预期 ROI", value=2.5, step=0.5)

if st.sidebar.button("🩺 运行复盘", type="primary", use_container_width=True):
    expected = {
        "goal": "gmv", "gmv": exp_gmv, "cvr": exp_cvr,
        "ctr": exp_ctr, "aov": exp_aov, "roi": exp_roi,
    }
    with st.spinner("复盘分析中..."):
        report = run_postmortem(campaign_id, district_id, days, expected)
        if report:
            st.session_state.pm_report = report
            st.rerun()

# Playbook history
with st.sidebar.expander("📚 历史 Playbook", expanded=False):
    if st.button("🔄 刷新", key="pm_refresh_playbooks"):
        st.session_state.pm_playbooks = None
    playbooks_data = st.session_state.pm_playbooks or fetch_playbooks(10)
    if playbooks_data and playbooks_data.get("playbooks"):
        st.session_state.pm_playbooks = playbooks_data
        for pb in playbooks_data["playbooks"]:
            if st.button(f"📖 {pb['title'][:25]}...", key=f"pb_{pb['id']}"):
                try:
                    detail = httpx.get(f"{API_BASE}/phase3/postmortem/playbooks/{pb['id']}", timeout=10).json()
                    st.session_state.pm_report = detail
                    st.rerun()
                except Exception:
                    pass
    else:
        st.caption("暂无 Playbook")

st.sidebar.divider()
st.sidebar.caption("Phase 3 · 复盘 Agent · 预期 vs 实际对比")

# ═══════════ Main Content ═══════════

report = st.session_state.pm_report

if not report:
    st.info("👈 请在左侧输入活动 ID 和预期指标，点击「运行复盘」开始分析")
    st.stop()

# ── Summary ──

result = report.get("result", {})
if isinstance(result, dict):
    # Goal achievement
    goal_ach = result.get("goal_achievement", {})
    if goal_ach:
        achieved = goal_ach.get("achieved", False)
        completion = goal_ach.get("completion_rate", 0)
        emoji = "✅" if achieved else "❌"
        color = "#28a745" if achieved else "#dc3545"

        st.markdown(f"### {emoji} 目标达成: {goal_ach.get('goal', '?').upper()}")
        col1, col2 = st.columns(2)
        col1.metric("达成率", f"{completion:.0%}")
        col2.metric("是否达标", "是" if achieved else "否")

    # Summary text
    summary = result.get("summary", "")
    if summary:
        st.markdown(f"> {summary}")

st.divider()

# ── Comparison Table ──

comparison = result.get("comparison", {}) if isinstance(result, dict) else report.get("comparison", {})
if comparison:
    st.subheader("📊 指标对比")

    comp_data = []
    for key, vals in comparison.items():
        if isinstance(vals, dict):
            status_emoji = {"above": "🟢", "below": "🔴", "on_target": "🟡"}.get(vals.get("status", ""), "⚪")
            comp_data.append({
                "指标": key.upper(),
                "预期": vals.get("expected", 0),
                "实际": vals.get("actual", 0),
                "偏差": f"{vals.get('delta_pct', 0):.1%}",
                "状态": f"{status_emoji} {vals.get('status', '?')}",
            })

    if comp_data:
        st.dataframe(comp_data, use_container_width=True, hide_index=True)

        # Bar chart
        fig = go.Figure()
        metrics = [d["指标"] for d in comp_data]
        fig.add_trace(go.Bar(name="预期", x=metrics, y=[d["预期"] for d in comp_data], marker_color="#6c757d"))
        fig.add_trace(go.Bar(name="实际", x=metrics, y=[d["实际"] for d in comp_data], marker_color="#0d6efd"))
        fig.update_layout(barmode="group", height=350, margin=dict(l=20, r=20, t=20, b=20))
        st.plotly_chart(fig, use_container_width=True)

st.divider()

# ── Analysis ──

analysis = result.get("analysis", {}) if isinstance(result, dict) else {}
if analysis:
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("✅ 做对了什么")
        for item in analysis.get("what_worked", []):
            impact_color = {"high": "#28a745", "medium": "#ffc107", "low": "#6c757d"}.get(item.get("impact", ""), "#6c757d")
            with st.container(border=True):
                st.markdown(f"**{item.get('factor', '')}** <span style='color:{impact_color}'>[{item.get('impact', '')}]</span>", unsafe_allow_html=True)
                st.caption(item.get("detail", ""))

    with col_right:
        st.subheader("❌ 需要改进")
        for item in analysis.get("what_didnt_work", []):
            impact_color = {"high": "#dc3545", "medium": "#fd7e14", "low": "#ffc107"}.get(item.get("impact", ""), "#6c757d")
            with st.container(border=True):
                st.markdown(f"**{item.get('factor', '')}** <span style='color:{impact_color}'>[{item.get('impact', '')}]</span>", unsafe_allow_html=True)
                st.caption(item.get("detail", ""))

    # Surprises
    surprises = analysis.get("surprises", [])
    if surprises:
        with st.expander("💡 意外发现", expanded=False):
            for s in surprises:
                st.markdown(f"**{s.get('finding', '')}**")
                st.caption(f"启示: {s.get('implication', '')}")

st.divider()

# ── Lessons Learned ──

lessons = result.get("lessons_learned", []) if isinstance(result, dict) else []
if lessons:
    st.subheader("📖 经验教训")
    for i, lesson in enumerate(lessons):
        with st.container(border=True):
            col_cat, col_conf = st.columns([3, 1])
            col_cat.markdown(f"**{i + 1}. [{lesson.get('category', '?')}] {lesson.get('lesson', '')}**")
            col_conf.metric("置信度", f"{lesson.get('confidence', 0):.0%}")
            if lesson.get("applicable_scenarios"):
                scenarios = " | ".join(lesson["applicable_scenarios"])
                st.caption(f"适用场景: {scenarios}")

st.divider()

# ── Playbook Candidate ──

playbook = result.get("playbook_candidate", {}) if isinstance(result, dict) else {}
if playbook and playbook.get("should_save"):
    st.subheader("📚 Playbook 候选")
    with st.container(border=True):
        st.markdown(f"**{playbook.get('title', '未命名')}**")
        st.write(playbook.get("strategy_summary", ""))
        if report.get("playbook_saved"):
            st.success(f"已保存 (ID: {report.get('playbook_doc_id', '?')})")

# ── Evidence ──

evidence = report.get("evidence", [])
if evidence:
    with st.expander("📎 数据证据", expanded=False):
        for e in evidence:
            st.caption(f"- {e}")

# ── Refresh ──
if st.button("🔄 刷新", key="pm_refresh_main"):
    st.rerun()
