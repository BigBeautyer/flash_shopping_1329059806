"""Workflow operation panel — create campaigns, review agent outputs, approve/reject."""

import streamlit as st
import httpx
import json
import time

API_BASE = "http://127.0.0.1:8000/api"


# ── State ──

if "campaign_id" not in st.session_state:
    st.session_state.campaign_id = None
if "workflow_status" not in st.session_state:
    st.session_state.workflow_status = None


# ── Helpers ──

def create_campaign(name: str, goal: str, district_id: int, categories: list, budget: float, constraints: dict) -> dict | None:
    try:
        r = httpx.post(f"{API_BASE}/workflow/campaigns", json={
            "name": name, "goal_type": goal, "district_id": district_id,
            "category_scope": categories, "budget": budget, "constraints": constraints,
        }, timeout=300)
        if r.status_code == 200:
            return r.json()
        st.error(f"API error: {r.text}")
        return None
    except Exception as e:
        st.error(f"Connection error: {e}")
        return None


def get_state(campaign_id: str) -> dict | None:
    try:
        r = httpx.get(f"{API_BASE}/workflow/campaigns/{campaign_id}/state", timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def approve_selection(campaign_id: str, approved: bool, approved_skus: list | None = None, rejected_skus: list | None = None, comment: str = "") -> dict | None:
    try:
        r = httpx.post(f"{API_BASE}/workflow/campaigns/{campaign_id}/approve-selection",
                       json={"approved": approved, "approved_skus": approved_skus or [], "rejected_skus": rejected_skus or [], "comment": comment}, timeout=300)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        st.error(f"Error: {e}")
        return None


def approve_final(campaign_id: str, approved: bool, comment: str = "") -> dict | None:
    try:
        r = httpx.post(f"{API_BASE}/workflow/campaigns/{campaign_id}/approve-final",
                       json={"approved": approved, "comment": comment}, timeout=300)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        st.error(f"Error: {e}")
        return None


# ── Page ──

st.title("⚙️ 闪购活动工作流")

# ═══ Phase 1: Create Campaign ═══

if st.session_state.campaign_id is None:
    st.subheader("创建新活动")

    with st.form("campaign_form"):
        name = st.text_input("活动名称", "午间闪购测试活动")
        col1, col2 = st.columns(2)
        with col1:
            goal = st.selectbox("活动目标", ["gmv", "roi", "new_user", "clearance"],
                                format_func=lambda x: {"gmv": "提GMV", "roi": "提ROI", "new_user": "拉新", "clearance": "清库存"}[x])
            budget = st.number_input("预算（元）", 1000, 50000, 5000, 1000)
        with col2:
            district_id = st.selectbox("商圈", [1, 2, 3, 4, 5],
                                       format_func=lambda x: {1: "望京", 2: "三里屯", 3: "国贸", 4: "中关村", 5: "五道口"}[x])
            margin_floor = st.slider("毛利下限", 0.10, 0.40, 0.18, 0.01)

        categories = st.multiselect("品类范围", ["水果", "饮料", "零食", "乳制品", "速食", "烘焙", "日用品", "酒类"],
                                     default=["水果", "饮料", "零食"])

        submitted = st.form_submit_button("🚀 启动 AI 工作流", type="primary", use_container_width=True)

        if submitted:
            if not categories:
                st.warning("请至少选择一个品类")
            else:
                with st.spinner("AI 工作流执行中... Planner → 选品Agent → 等待审核..."):
                    constraints = {"gross_margin_floor": margin_floor, "inventory_min": 50}
                    result = create_campaign(name, goal, district_id, categories, budget, constraints)
                    if result:
                        st.session_state.campaign_id = result["campaign_id"]
                        st.session_state.workflow_status = result["status"]
                        st.success(f"活动创建成功！ID: {result['campaign_id']}")
                        st.rerun()

# ═══ Phase 2: Review Workflow ═══

else:
    cid = st.session_state.campaign_id
    state = get_state(cid)

    st.subheader(f"活动: {cid}")
    st.caption(f"状态: {state.get('status', 'unknown') if state else 'loading...'}")

    if not state:
        st.warning("无法获取活动状态，请检查后端是否运行")
        if st.button("← 返回创建"):
            st.session_state.campaign_id = None
            st.rerun()
        st.stop()

    status = state.get("status", "draft")
    district_names = {1: "望京", 2: "三里屯", 3: "国贸", 4: "中关村", 5: "五道口"}

    # ── Progress Bar ──
    progress_map = {
        "draft": 0.0,
        "awaiting_selection_review": 0.40,
        "running": 0.60,
        "awaiting_final_review": 0.85,
        "completed": 1.0,
    }
    st.progress(progress_map.get(status, 0.0), text=f"工作流进度 · {district_names.get(state.get('district_id', 1), '?')}商圈")

    st.divider()

    # ── Selection Review ──
    if status in ("awaiting_selection_review",) and state.get("selection_output"):
        st.subheader("🔍 选品审核")
        sel_output = state.get("selection_output") or {}
        sel = sel_output.get("result") or {}
        selection_list = sel.get("selection_list", [])
        summary = sel.get("summary", {})

        if summary:
            cols = st.columns(4)
            cols[0].metric("推荐商品数", summary.get("total_skus", len(selection_list)))
            cols[1].metric("预期总GMV", f"¥{summary.get('expected_total_gmv', 0):,.0f}")
            if summary.get("category_distribution"):
                cols[2].metric("品类覆盖", str(len(summary.get("category_distribution", {}))))
            cols[3].metric("置信度", f"{sel_output.get('confidence', 0):.0%}")

        # Evidence
        evidence = sel_output.get("evidence", [])
        if evidence:
            with st.expander("📋 选品依据", expanded=False):
                for e in evidence:
                    st.write(f"• {e}")

        # Product table with checkboxes
        if selection_list:
            st.write("**选品清单（勾选=通过，取消=驳回）：**")

            # Initialize session state for checkboxes
            if f"sel_checkboxes_{cid}" not in st.session_state:
                st.session_state[f"sel_checkboxes_{cid}"] = {
                    p["sku_id"]: True for p in selection_list
                }

            # Select all / deselect all
            col_all, col_none, _ = st.columns([1, 1, 4])
            with col_all:
                if st.button("☑️ 全选", use_container_width=True):
                    for p in selection_list:
                        st.session_state[f"sel_checkboxes_{cid}"][p["sku_id"]] = True
                    st.rerun()
            with col_none:
                if st.button("☐ 取消全选", use_container_width=True):
                    for p in selection_list:
                        st.session_state[f"sel_checkboxes_{cid}"][p["sku_id"]] = False
                    st.rerun()

            # Render table — sync checkboxes to sel_checkboxes dict at approval time
            checked_count = 0
            for p in selection_list[:20]:
                sku = p["sku_id"]
                risk_emoji = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(p.get("risk_level", ""), "⚪")
                cols = st.columns([0.5, 2, 1, 1, 1, 0.8])
                cols[0].checkbox(
                    "✓", value=st.session_state[f"sel_checkboxes_{cid}"].get(sku, True),
                    key=f"chk_{cid}_{sku}",
                    label_visibility="collapsed",
                )
                cols[1].write(f"**{p.get('name', sku)}**  _{p.get('category', '')}_")
                cols[2].write(f"评分: {p.get('recommend_score', 0):.2f}")
                cols[3].write(f"GMV: ¥{p.get('expected_gmv', 0):,.0f}")
                cols[4].write(f"{risk_emoji} {p.get('risk_level', '')}")
                cols[5].write(f"库存: {p.get('inventory', 0)}")
                if st.session_state[f"sel_checkboxes_{cid}"].get(sku, True):
                    checked_count += 1

            st.caption(f"已选 {checked_count} / {len(selection_list)} 个商品")

        # Approval buttons
        st.divider()
        col_a, col_b = st.columns([1, 3])
        with col_a:
            if st.button("✅ 通过选品", type="primary", use_container_width=True):
                # Sync checkbox widget values into sel_checkboxes dict
                for p in selection_list:
                    sku = p["sku_id"]
                    st.session_state[f"sel_checkboxes_{cid}"][sku] = st.session_state.get(f"chk_{cid}_{sku}", True)
                approved_skus = [
                    sku for sku, checked in st.session_state.get(f"sel_checkboxes_{cid}", {}).items()
                    if checked
                ]
                rejected_skus = [
                    sku for sku, checked in st.session_state.get(f"sel_checkboxes_{cid}", {}).items()
                    if not checked
                ]
                with st.spinner("审批中..."):
                    result = approve_selection(cid, True, approved_skus, rejected_skus)
                    if result:
                        del st.session_state[f"sel_checkboxes_{cid}"]
                        st.session_state.workflow_status = result.get("status")
                        st.rerun()
        with col_b:
            if st.button("❌ 全部驳回，重新选品", use_container_width=True):
                with st.spinner("驳回中..."):
                    result = approve_selection(cid, False, [], [])
                    if result:
                        del st.session_state[f"sel_checkboxes_{cid}"]
                        st.session_state.workflow_status = result.get("status")
                        st.rerun()

    # ── Final Review ──
    elif status in ("awaiting_final_review",) or (status in ("running",) and state.get("final_review_output")):
        st.subheader("🏁 终审")

        # Show pricing
        pricing = state.get("pricing_output", {}) if state.get("pricing_output") else {}
        pricing_result = pricing.get("result", {})
        pricing_list = pricing_result.get("pricing_list", [])
        if pricing_list:
            with st.expander("💰 定价方案", expanded=True):
                pr_data = []
                for p in pricing_list[:10]:
                    pr_data.append({
                        "商品": p.get("name", p.get("sku_id", "")),
                        "原价": f"¥{p.get('original_price', 0):.2f}",
                        "建议价": f"¥{p.get('suggested_price', 0):.2f}",
                        "折扣": f"{p.get('discount', 0):.0%}",
                        "毛利率": f"{p.get('gross_margin_after', 0):.0%}",
                        "风险": ", ".join(p.get("risk_flags", [])) or "无",
                    })
                st.dataframe(pr_data, use_container_width=True, hide_index=True)

        # Show copy
        copies = state.get("copywriting_output", {}) if state.get("copywriting_output") else {}
        copy_result = copies.get("result", {})
        copy_list = copy_result.get("copy_list", [])
        if copy_list:
            with st.expander("✍️ 营销文案", expanded=True):
                for c in copy_list[:5]:
                    st.markdown(f"**{c.get('product_name', c.get('sku_id', ''))}**")
                    versions = c.get("versions", {})
                    st.text(f"标题: {versions.get('title', '')}")
                    st.text(f"卖点: {versions.get('selling_point', '')}")
                    st.divider()

        # Show review
        final_review = state.get("final_review_output", {}) if state.get("final_review_output") else {}
        review_result = final_review.get("result", {})
        verdict = review_result.get("overall_verdict", "unknown")
        issues = review_result.get("issues", [])
        stats = review_result.get("stats", {})

        if stats:
            cols = st.columns(4)
            cols[0].metric("检测项", stats.get("total_checked", 0))
            cols[1].metric("通过", stats.get("passed", 0))
            cols[2].metric("警告", stats.get("warnings", 0))
            cols[3].metric("拦截", stats.get("blocked", 0))

        if issues:
            with st.expander(f"⚠️ 审核问题 ({len(issues)}项)", expanded=True):
                for iss in issues:
                    sev = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(iss.get("severity", ""), "")
                    st.warning(f"{sev} **{iss.get('type', '')}** — {iss.get('description', '')}")
                    st.caption(f"建议: {iss.get('suggestion', '')}")

        # Approval
        verdict_label = {"pass": "✅ 审核通过", "pass_with_warnings": "⚠️ 有警告但可上线",
                         "needs_revision": "🔄 需要修改", "blocked": "🚫 被拦截"}
        st.info(f"审核结论: {verdict_label.get(verdict, verdict)}")

        st.divider()
        col_a, col_b, _ = st.columns([1, 1, 3])
        with col_a:
            if st.button("✅ 终审批复", type="primary", use_container_width=True):
                with st.spinner("终审中..."):
                    result = approve_final(cid, True)
                    if result:
                        st.session_state.workflow_status = result.get("status")
                        st.rerun()
        with col_b:
            if st.button("❌ 驳回修改", use_container_width=True):
                with st.spinner("驳回中..."):
                    result = approve_final(cid, False)
                    if result:
                        st.session_state.workflow_status = result.get("status")
                        st.rerun()

    # ── Completed ──
    elif status == "completed":
        st.success("🎉 活动审批完成，可以上线！")
        if st.button("← 创建新活动", type="primary"):
            st.session_state.campaign_id = None
            st.session_state.workflow_status = None
            st.rerun()

    # ── Refresh ──
    st.divider()
    if st.button("🔄 刷新状态"):
        st.rerun()
