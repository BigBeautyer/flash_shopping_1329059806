"""Agent Replay panel — timeline visualization of agent execution history."""

import streamlit as st
import plotly.graph_objects as go
import httpx
import pandas as pd

API_BASE = "http://127.0.0.1:8000/api"

# ── State ──
if "replay_runs" not in st.session_state:
    st.session_state.replay_runs = None
if "replay_detail" not in st.session_state:
    st.session_state.replay_detail = None
if "replay_timeline" not in st.session_state:
    st.session_state.replay_timeline = None
if "replay_agents" not in st.session_state:
    st.session_state.replay_agents = []


# ── Helpers ──

@st.cache_data(ttl=15)
def fetch_agent_types() -> list:
    try:
        r = httpx.get(f"{API_BASE}/phase3/replay/agents", timeout=10)
        return r.json().get("agents", []) if r.status_code == 200 else []
    except Exception:
        return []


def fetch_runs(agent_name: str = "", task_id: str = "", limit: int = 50) -> dict | None:
    try:
        params = {"limit": limit}
        if agent_name:
            params["agent_name"] = agent_name
        if task_id:
            params["task_id"] = task_id
        r = httpx.get(f"{API_BASE}/phase3/replay/runs", params=params, timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        st.error(f"获取记录失败: {e}")
        return None


def fetch_run_detail(run_id: str) -> dict | None:
    try:
        r = httpx.get(f"{API_BASE}/phase3/replay/runs/{run_id}", timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        st.error(f"获取详情失败: {e}")
        return None


def fetch_timeline(task_id: str) -> dict | None:
    try:
        r = httpx.get(f"{API_BASE}/phase3/replay/timeline", params={"task_id": task_id}, timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        st.error(f"获取时间线失败: {e}")
        return None


def render_run_detail(detail: dict):
    """Render the run detail section. Called inline from either tab."""
    st.divider()
    st.subheader(f"🔬 Agent: {detail.get('agent_name', '?')}")
    st.caption(f"Run ID: {detail.get('run_id', '?')}")
    st.caption(f"Task ID: {detail.get('task_id', '?')}")
    st.caption(f"执行时间: {detail.get('created_at', '?')}")

    col1, col2, col3 = st.columns(3)
    col1.metric("延迟", f"{detail.get('latency_ms', 0)}ms")
    col2.metric("Token", detail.get('tokens_used', 0))
    col3.metric("错误", "是" if detail.get("error") else "否")

    out_sum = detail.get("output_summary", {})
    if out_sum:
        st.divider()
        st.subheader("输出摘要")
        col_c, col_r, col_h = st.columns(3)
        col_c.metric("置信度", f"{out_sum.get('confidence', 0):.0%}")
        risk_level = out_sum.get("risk_level", "low")
        risk_emoji = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(risk_level, "⚪")
        col_r.metric("风险等级", f"{risk_emoji} {risk_level}")
        col_h.metric("需人工审核", "是" if out_sum.get("need_human_review") else "否")

        if out_sum.get("recommended_action"):
            st.info(f"💡 建议动作: {out_sum['recommended_action']}")

        if out_sum.get("evidence"):
            with st.expander("📎 决策依据", expanded=False):
                for e in out_sum["evidence"]:
                    st.caption(f"- {e}")

    output_result = detail.get("output_result", {})
    if output_result:
        st.divider()
        st.subheader("完整输出")
        with st.expander("查看完整结果 JSON", expanded=False):
            st.json(output_result)

    st.divider()
    st.subheader("输入协议")
    with st.expander("查看完整输入 JSON", expanded=False):
        st.json(detail.get("input", {}))


# ── Page ──

st.title("⏪ Agent 执行回放")
st.caption("Phase 3 · 决策时间线 · 输入输出审计 · 性能分析")

# ── Sidebar Filters ──

st.sidebar.title("🔍 筛选")

agent_filter = st.sidebar.selectbox(
    "Agent 类型",
    ["全部"] + fetch_agent_types(),
    key="agent_filter",
)
task_id_filter = st.sidebar.text_input("Task/Campaign ID", placeholder="例如: CAMP_20260507")
limit = st.sidebar.slider("返回条数", 10, 200, 50)

if st.sidebar.button("🔍 查询", type="primary", use_container_width=True):
    agent_name = "" if agent_filter == "全部" else agent_filter
    runs = fetch_runs(agent_name, task_id_filter, limit)
    if runs:
        st.session_state.replay_runs = runs
        st.session_state.replay_detail = None  # clear detail when re-querying
        st.rerun()

# Timeline viewer
with st.sidebar.expander("⏱️ 时间线查看器", expanded=False):
    timeline_task = st.text_input("输入 Task ID", key="timeline_task", placeholder="CAMP_20260507")
    if st.button("查看时间线", key="btn_timeline", use_container_width=True):
        timeline = fetch_timeline(timeline_task)
        if timeline:
            st.session_state.replay_timeline = timeline
            st.rerun()

st.sidebar.divider()
st.sidebar.caption("Phase 3 · 回放 Agent · AgentRunLog 审计")

# ═══════════ Main Content ═══════════

tab_runs, tab_timeline = st.tabs(["📋 执行记录", "⏱️ 时间线"])

# ═══════════════════════════════════════════════
# Tab 1: Run List + Inline Detail
# ═══════════════════════════════════════════════

with tab_runs:
    detail = st.session_state.replay_detail

    # If viewing a detail, show "back to list" + detail inline
    if detail:
        if st.button("← 返回列表", key="back_to_list"):
            st.session_state.replay_detail = None
            st.rerun()
        render_run_detail(detail)
        st.stop()

    # ── Run list ──
    runs = st.session_state.replay_runs

    if not runs:
        st.info("👈 请在左侧设置筛选条件并点击「查询」加载执行记录")
        st.stop()

    run_list = runs.get("runs", [])
    total = runs.get("total", 0)
    st.caption(f"共 {total} 条记录，显示 {len(run_list)} 条")

    if not run_list:
        st.warning("没有找到匹配的执行记录")
        st.stop()

    # Summary stats
    df = pd.DataFrame(run_list)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("总执行次数", len(run_list))
    col2.metric("平均延迟", f"{df['latency_ms'].mean():.0f}ms" if "latency_ms" in df else "?")
    col3.metric("错误次数", int(df["has_error"].sum()) if "has_error" in df else 0)
    col4.metric("Agent 类型数", df["agent_name"].nunique() if "agent_name" in df else 0)

    st.divider()

    # Run list
    for i, run in enumerate(run_list):
        err_emoji = "❌" if run.get("has_error") else "✅"
        with st.container(border=True):
            col_info, col_meta, col_action = st.columns([3, 2, 1])
            col_info.markdown(f"**{err_emoji} {run.get('agent_name', '?')}**")
            col_info.caption(f"Run: {run.get('run_id', '?')[:40]}...")
            col_meta.caption(f"Task: {run.get('task_id', '?')[:30]}...")
            col_meta.caption(f"延迟: {run.get('latency_ms', 0)}ms")
            col_meta.caption(f"时间: {run.get('created_at', '?')[:19]}")
            if col_action.button("查看", key=f"view_run_{run.get('run_id', i)}"):
                detail_data = fetch_run_detail(run["run_id"])
                if detail_data:
                    st.session_state.replay_detail = detail_data
                    st.rerun()

    if st.button("🔄 刷新", key="replay_refresh"):
        st.session_state.replay_runs = None
        st.session_state.replay_detail = None
        st.rerun()

# ═══════════════════════════════════════════════
# Tab 2: Timeline
# ═══════════════════════════════════════════════

with tab_timeline:
    timeline = st.session_state.replay_timeline

    if not timeline:
        st.info("在左侧「时间线查看器」输入 Task ID 并点击「查看时间线」")
        st.stop()

    steps = timeline.get("timeline", [])
    st.subheader(f"决策时间线 · Task: {timeline.get('task_id', '?')}")
    st.caption(f"共 {timeline.get('total_steps', 0)} 个步骤")

    if not steps:
        st.warning("该任务没有找到执行记录")
        st.stop()

    # Timeline chart
    fig = go.Figure()

    agent_names = [s.get("agent_name", "?") for s in steps]
    latencies = [s.get("latency_ms", 0) for s in steps]
    confidences = [s.get("confidence", 0) for s in steps]
    risk_levels = [s.get("risk_level", "low") for s in steps]
    step_nums = list(range(1, len(steps) + 1))

    risk_colors = {"low": "#28a745", "medium": "#ffc107", "high": "#dc3545"}

    fig.add_trace(go.Scatter(
        x=step_nums,
        y=latencies,
        mode="markers+lines+text",
        name="延迟 (ms)",
        marker=dict(
            size=20,
            color=[risk_colors.get(r, "#6c757d") for r in risk_levels],
        ),
        text=agent_names,
        textposition="top center",
        hovertemplate="<b>%{text}</b><br>Step: %{x}<br>延迟: %{y}ms<br>置信度: %{customdata:.0%}<extra></extra>",
        customdata=confidences,
    ))

    fig.update_layout(
        title="Agent 执行时间线",
        xaxis=dict(title="执行顺序", tickmode="linear", tick0=1, dtick=1),
        yaxis=dict(title="延迟 (ms)"),
        height=400,
        margin=dict(l=20, r=20, t=40, b=20),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Step cards
    for step in steps:
        risk_emoji = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(step.get("risk_level", "low"), "⚪")
        err_emoji = "❌" if step.get("has_error") else ""
        with st.container(border=True):
            col_s, col_a, col_m = st.columns([1, 3, 2])
            col_s.markdown(f"### Step {step.get('step', '?')}")
            col_a.markdown(f"**{err_emoji} {step.get('agent_name', '?')}** {risk_emoji}")
            col_a.caption(f"延迟: {step.get('latency_ms', 0)}ms | 置信度: {step.get('confidence', 0):.0%}")
            col_m.caption(f"时间: {step.get('created_at', '?')[:19]}")
            if step.get("recommended_action"):
                col_a.caption(f"动作: {step['recommended_action']}")
