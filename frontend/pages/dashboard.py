"""Funnel dashboard V1 — 全链路漏斗看板 + 北极星指标."""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import httpx
from datetime import datetime

API_BASE = "http://127.0.0.1:8000/api"


# ── Helpers ──

@st.cache_data(ttl=60)
def fetch_snapshot(district_id: int, days: int = 7) -> dict:
    try:
        r = httpx.get(f"{API_BASE}/dashboard/snapshot", params={"district_id": district_id, "days": days}, timeout=10)
        return r.json()
    except Exception:
        return {}


@st.cache_data(ttl=60)
def fetch_trend(district_id: int, days: int = 30) -> list:
    try:
        r = httpx.get(f"{API_BASE}/dashboard/trend", params={"district_id": district_id, "days": days}, timeout=10)
        return r.json()
    except Exception:
        return []


@st.cache_data(ttl=300)
def fetch_districts() -> list:
    try:
        r = httpx.get(f"{API_BASE}/dashboard/districts", timeout=10)
        return r.json()
    except Exception:
        return []


# ── Sidebar ──

st.sidebar.title("⚡ AI闪购运营中台")

districts = fetch_districts()
district_options = {d["name"]: d["id"] for d in districts} if districts else {"模拟商圈": 1}
selected_name = st.sidebar.selectbox("选择商圈", list(district_options.keys()))
district_id = district_options[selected_name]

days = st.sidebar.slider("统计周期（天）", 7, 90, 30, 7)

# ── Data ──

snapshot = fetch_snapshot(district_id, days)
trend = fetch_trend(district_id, days)

# ── Title ──

st.title(f"📊 {selected_name} — 数据看板")
st.caption(f"统计周期：最近 {days} 天 | 更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")

# ── Metric Cards Row ──

funnel = snapshot.get("funnel", {})
cols = st.columns(6)
metrics_config = [
    ("曝光UV", funnel.get("exposure_uv", 0), None),
    ("CTR", f"{funnel.get('ctr', 0):.2%}", None),
    ("加购率", f"{funnel.get('cart_rate', 0):.2%}", None),
    ("转化率", f"{funnel.get('cvr', 0):.2%}", None),
    ("GMV", f"¥{snapshot.get('gmv', 0):,.0f}", None),
    ("客单价", f"¥{snapshot.get('aov', 0):,.2f}", None),
]
for col, (label, val, _) in zip(cols, metrics_config):
    col.metric(label, val)

cols2 = st.columns(4)
metrics_config2 = [
    ("毛利率", f"{snapshot.get('gross_margin', 0):.2%}"),
    ("动销率", f"{snapshot.get('sell_through_rate', 0):.2%}"),
    ("复购率", f"{funnel.get('repurchase_rate', 0):.2%}"),
    ("支付UV", funnel.get("payment_uv", 0)),
]
for col, (label, val) in zip(cols2, metrics_config2):
    col.metric(label, val)

st.divider()

# ── Funnel Chart ──

st.subheader("全链路漏斗")

funnel_stages = ["曝光", "点击", "加购", "支付", "复购"]
funnel_values = [
    funnel.get("exposure_uv", 0),
    funnel.get("click_uv", 0),
    funnel.get("cart_uv", 0),
    funnel.get("payment_uv", 0),
    funnel.get("repurchase_uv", 0),
]
funnel_rates = ["", f"{funnel.get('ctr', 0):.2%}", f"{funnel.get('cart_rate', 0):.2%}", f"{funnel.get('cvr', 0):.2%}", f"{funnel.get('repurchase_rate', 0):.2%}"]

funnel_v = [
    funnel.get("exposure_uv", 0),
    funnel.get("click_uv", 0),
    funnel.get("cart_uv", 0),
    funnel.get("payment_uv", 0),
    funnel.get("repurchase_uv", 0),
]
funnel_r = [
    "",
    f"CTR {funnel.get('ctr', 0):.1%}",
    f"加购率 {funnel.get('cart_rate', 0):.1%}",
    f"转化率 {funnel.get('cvr', 0):.1%}",
    f"复购率 {funnel.get('repurchase_rate', 0):.1%}",
]
funnel_labels = [
    f"<b>{s}</b><br>{v:,}<br>{r}" if r else f"<b>{s}</b><br>{v:,}"
    for s, v, r in zip(funnel_stages, funnel_v, funnel_r)
]

fig_funnel = go.Figure(go.Funnel(
    y=funnel_stages,
    x=funnel_v,
    text=funnel_labels,
    textposition="outside",
    textinfo="text",
    textfont=dict(size=15, color="#1a1a2e", family="Arial, sans-serif"),
    marker={
        "color": ["#7ec8e3", "#a8d8a8", "#c4a8d4", "#f7c59f", "#f7a8b8"],
        "line": {"width": 1.5, "color": "#444"},
    },
    connector={"fillcolor": "rgba(180,180,180,0.2)", "line": {"width": 1, "color": "#aaa"}},
))
fig_funnel.update_layout(
    height=460,
    margin=dict(t=10, b=10, l=10, r=180),  # extra right margin for outside text
    uniformtext=dict(minsize=13, mode="show"),
)
st.plotly_chart(fig_funnel, use_container_width=True)

# ── Trend Chart ──

st.subheader("GMV 趋势")

if trend:
    df_trend = pd.DataFrame(trend)
    df_trend["date"] = pd.to_datetime(df_trend["date"])

    col1, col2 = st.columns(2)

    with col1:
        fig_gmv = px.line(df_trend, x="date", y="gmv", markers=True, title="GMV 日趋势")
        fig_gmv.update_traces(marker=dict(size=6, color="#636EFA", line=dict(width=1, color="white")))
        fig_gmv.update_layout(height=300, margin=dict(t=30, b=0))
        st.plotly_chart(fig_gmv, use_container_width=True)

    with col2:
        fig_funnel_trend = go.Figure()
        for col_name, label, color in [
            ("exposure_uv", "曝光", "#7ec8e3"),
            ("click_uv", "点击", "#a8d8a8"),
            ("payment_uv", "支付", "#f7c59f"),
        ]:
            fig_funnel_trend.add_trace(go.Scatter(
                x=df_trend["date"], y=df_trend[col_name],
                mode="lines+markers", name=label,
                line=dict(color=color, width=2),
                marker=dict(size=6, color=color, line=dict(width=1, color="white")),
            ))
        fig_funnel_trend.update_layout(title="漏斗各层日趋势", height=300, margin=dict(t=30, b=0))
        st.plotly_chart(fig_funnel_trend, use_container_width=True)
else:
    st.info("暂无趋势数据，请先生成模拟数据。")

st.divider()
st.caption("Phase 0 · 漏斗看板 V1 · 12 北极星指标 + 6 护栏指标")
