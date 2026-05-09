"""AB Experiment panel — design, run, and analyze controlled experiments."""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import httpx
import pandas as pd

API_BASE = "http://127.0.0.1:8000/api"

# ── State ──
if "ab_result" not in st.session_state:
    st.session_state.ab_result = None
if "ab_matrix" not in st.session_state:
    st.session_state.ab_matrix = None

DISTRICT_NAMES = {1: "望京", 2: "三里屯", 3: "国贸", 4: "中关村", 5: "五道口"}
METRICS = ["gmv", "cvr", "ctr", "aov"]


# ── Helpers ──

def run_ab(name: str, hypothesis: str, ctrl: int, treat: int, metric: str, days: int) -> dict | None:
    try:
        r = httpx.post(f"{API_BASE}/phase3/ab/run", json={
            "experiment_name": name, "hypothesis": hypothesis,
            "control_district": ctrl, "treatment_district": treat,
            "metric": metric, "days": days,
        }, timeout=120)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        st.error(f"实验失败: {e}")
        return None


@st.cache_data(ttl=30)
def fetch_experiments(limit: int = 20) -> dict | None:
    try:
        r = httpx.get(f"{API_BASE}/phase3/ab/experiments", params={"limit": limit}, timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def run_comparison_matrix(district_ids: list, metric: str, days: int) -> dict | None:
    try:
        r = httpx.post(f"{API_BASE}/phase3/ab/compare", json={
            "district_ids": district_ids, "metric": metric, "days": days,
        }, timeout=120)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        st.error(f"对比失败: {e}")
        return None


# ── Page ──

st.title("🧪 AB 实验")
st.caption("Phase 3 · 对照实验 · t 检验 · 效应量分析")

tab_run, tab_history, tab_matrix = st.tabs(["🚀 运行实验", "📋 历史记录", "📊 对比矩阵"])

# ═══════════════════════════════════════════════
# Tab 1: Run Experiment
# ═══════════════════════════════════════════════

with tab_run:
    col_ctrl, col_treat = st.columns(2)

    with col_ctrl:
        st.subheader("🔵 对照组")
        control_district = st.selectbox("商圈", list(DISTRICT_NAMES.keys()),
            format_func=lambda x: DISTRICT_NAMES[x], key="ctrl_district")

    with col_treat:
        st.subheader("🟠 实验组")
        treatment_district = st.selectbox("商圈", list(DISTRICT_NAMES.keys()),
            format_func=lambda x: DISTRICT_NAMES[x], key="treat_district")

    if control_district == treatment_district:
        st.warning("对照组和实验组不能是同一个商圈")

    exp_name = st.text_input("实验名称", value=f"{DISTRICT_NAMES[control_district]} vs {DISTRICT_NAMES[treatment_district]} 对比实验")
    hypothesis = st.text_input("实验假设", value="", placeholder="例如：三里屯的高消费人群会带来更高的 AOV")
    metric = st.selectbox("观测指标", METRICS, format_func=lambda x: x.upper())
    days = st.slider("实验周期（天）", 1, 90, 7)

    if st.button("🧪 运行实验", type="primary", use_container_width=True,
                  disabled=(control_district == treatment_district)):
        with st.spinner(f"运行 AB 实验中... 指标: {metric.upper()}"):
            result = run_ab(exp_name, hypothesis, control_district, treatment_district, metric, days)
            if result:
                st.session_state.ab_result = result
                st.rerun()

    # ── Results ──

    ab = st.session_state.ab_result
    if ab:
        st.divider()
        results = ab.get("results", {})

        # Significance banner
        sig = results.get("significant", False)
        p_val = results.get("p_value", 1.0)
        if sig:
            st.success(f"✅ 统计显著 (p = {p_val:.4f} < 0.05)")
        else:
            st.info(f"📊 未达统计显著 (p = {p_val:.4f})")

        # Key metrics
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("对照组均值", f"{results.get('control_mean', 0):,.2f}")
        col2.metric("实验组均值", f"{results.get('treatment_mean', 0):,.2f}",
                    delta=f"{results.get('relative_lift', 0):.1%}" if results.get('relative_lift') else None)
        col3.metric("效应量 (d)", f"{results.get('effect_size_cohens_d', 0):.3f}")
        col4.metric("p 值", f"{p_val:.4f}")

        # Effect interpretation
        effect_interp = results.get("effect_interpretation", "negligible")
        effect_descriptions = {
            "negligible": "效应量极小，两组差异可忽略",
            "small": "小效应，有轻微差异",
            "medium": "中等效应，差异值得关注",
            "large": "大效应，差异显著且实际意义明显",
        }
        st.caption(f"效应量解读: {effect_descriptions.get(effect_interp, '')}")

        # Bar chart
        fig = go.Figure()
        groups = [
            f"对照组\n({DISTRICT_NAMES.get(ab['control_group'].get('district_id', 0), '?')}, n={ab['control_group'].get('sample_size', 0)})",
            f"实验组\n({DISTRICT_NAMES.get(ab['treatment_group'].get('district_id', 0), '?')}, n={ab['treatment_group'].get('sample_size', 0)})",
        ]
        fig.add_trace(go.Bar(
            x=groups,
            y=[results.get("control_mean", 0), results.get("treatment_mean", 0)],
            marker_color=["#0d6efd", "#fd7e14"],
            text=[f"{results.get('control_mean', 0):,.2f}", f"{results.get('treatment_mean', 0):,.2f}"],
            textposition="outside",
        ))
        fig.update_layout(
            title=f"{metric.upper()} 对照组 vs 实验组",
            height=400, margin=dict(l=20, r=20, t=40, b=20),
            yaxis_title=metric.upper(),
        )
        st.plotly_chart(fig, use_container_width=True)

        # Details
        with st.expander("🔬 实验详情", expanded=False):
            st.json(ab)

        if st.button("🔄 清除结果"):
            st.session_state.ab_result = None
            st.rerun()

# ═══════════════════════════════════════════════
# Tab 2: History
# ═══════════════════════════════════════════════

with tab_history:
    exps_data = fetch_experiments(20)
    if exps_data and exps_data.get("experiments"):
        for exp in exps_data["experiments"]:
            sig_emoji = "✅" if exp.get("significant") else "📊"
            with st.container(border=True):
                col_info, col_metrics, col_sig = st.columns([3, 2, 2])
                col_info.markdown(f"**{sig_emoji} {exp.get('experiment_name', '?')}**")
                col_info.caption(f"假设: {exp.get('hypothesis', '')[:80]}...")
                col_metrics.metric("对照组", f"{exp.get('control_value', 0):,.2f}")
                col_metrics.metric("实验组", f"{exp.get('treatment_value', 0):,.2f}")
                col_sig.metric("p 值", f"{exp.get('p_value', 1):.4f}")
                col_sig.caption(f"指标: {exp.get('metric_name', '?')} | {'显著' if exp.get('significant') else '不显著'} | {exp.get('created_at', '?')[:10]}")
    else:
        st.info("暂无实验记录，请先运行实验")

# ═══════════════════════════════════════════════
# Tab 3: Comparison Matrix
# ═══════════════════════════════════════════════

with tab_matrix:
    st.subheader("多商圈对比矩阵")
    st.caption("对选中的所有商圈做两两对比，按 p 值排序，快速发现差异最大的商圈对")

    selected_districts = st.multiselect(
        "选择商圈", list(DISTRICT_NAMES.keys()),
        default=[1, 2, 3, 4, 5],
        format_func=lambda x: DISTRICT_NAMES[x],
    )
    matrix_metric = st.selectbox("观测指标", METRICS, format_func=lambda x: x.upper(), key="matrix_metric")
    matrix_days = st.slider("周期（天）", 1, 90, 7, key="matrix_days")

    if st.button("📊 运行对比矩阵", use_container_width=True, disabled=len(selected_districts) < 2):
        with st.spinner("运行两两对比..."):
            matrix = run_comparison_matrix(selected_districts, matrix_metric, matrix_days)
            if matrix:
                st.session_state.ab_matrix = matrix
                st.rerun()

    mat = st.session_state.ab_matrix
    if mat:
        comparisons = mat.get("comparisons", [])
        if comparisons:
            st.subheader(f"对比结果 ({len(comparisons)} 对)，按显著性排序")

            # Heatmap data
            matrix_data = []
            for c in comparisons:
                sig_emoji = "✅" if c.get("significant") else ""
                matrix_data.append({
                    "对照组": DISTRICT_NAMES.get(c["control_district"], "?"),
                    "实验组": DISTRICT_NAMES.get(c["treatment_district"], "?"),
                    "对照组均值": f"{c['control_mean']:,.2f}",
                    "实验组均值": f"{c['treatment_mean']:,.2f}",
                    "提升": f"{c['relative_lift']:.1%}",
                    "p值": f"{c['p_value']:.4f}",
                    "显著": "✅" if c.get("significant") else "",
                })

            st.dataframe(matrix_data, use_container_width=True, hide_index=True)

            # Highlight chart
            fig = go.Figure()
            lifts = [c["relative_lift"] for c in comparisons]
            labels = [f"{DISTRICT_NAMES[c['control_district']]}→{DISTRICT_NAMES[c['treatment_district']]}" for c in comparisons]
            colors = ["#28a745" if c["significant"] else "#6c757d" for c in comparisons]

            fig.add_trace(go.Bar(
                y=labels, x=lifts, orientation="h",
                marker_color=colors,
                text=[f"{l:.1%}" for l in lifts],
                textposition="outside",
            ))
            fig.update_layout(
                title=f"{matrix_metric.upper()} 商圈间提升率对比",
                height=max(300, len(labels) * 40),
                margin=dict(l=20, r=60, t=40, b=20),
                xaxis_title="相对提升",
            )
            st.plotly_chart(fig, use_container_width=True)
