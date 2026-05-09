# AI闪购业务 MVP 开发计划 v2

> 更新说明：v2 吸收了 GPT-5.5 方案的精华，增加了 Planner Agent、标准化任务协议、统一输出规范、漏斗分层归因、复盘Agent、治理层、反馈闭环等关键设计。分阶段策略从单次冲刺调整为三阶段演进。

## 一、项目定位

**一句话描述**：以 Multi-Agent 为核心，将闪购运营的选品、定价、营销、审核、监控、诊断、复盘全流程产品化，构建可配置工作流、推荐系统和数据闭环。

**核心原则**：结构化决策靠规则/模型，LLM 负责理解、编排、解释和生成。不追求"全自动"，先做"辅助决策+人审卡点"。

---

## 二、系统架构（5层）

```
┌──────────────────────────────────────────────────────────────────┐
│                     5. 治理层                                     │
│  权限控制 │ 审计日志 │ Prompt版本管理 │ 模型评估 │ Agent执行回放  │
└──────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────┐
│                     4. 应用层 (Streamlit)                         │
│  活动工作台 │ 选品推荐页 │ 文案审核页 │ 诊断面板 │ 看板 │ 复盘中心 │
└──────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────┐
│                     3. Agent编排层 (LangGraph)                    │
│  ┌──────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐  │
│  │ Planner  │→│ 选品    │→│ 定价    │→│ 文案    │→│ 审核    │  │
│  │ Agent    │ │ Agent   │ │ Agent   │ │ Agent   │ │ Agent   │  │
│  └──────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘  │
│       ↑                                    ↓                     │
│  ┌──────────┐                        🛑 人审卡点1 + 卡点2        │
│  │ 复盘     │ ←──────────────────────────┘                      │
│  │ Agent    │                                                    │
│  └──────────┘   ┌──────────────────┐                             │
│                 │ 诊断Agent(常驻)   │ 持续监控已上线活动           │
│                 └──────────────────┘                             │
│  所有 Agent 间通信走 标准化任务协议 (TaskProtocol JSON)           │
└──────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────┐
│                     2. 特征与策略层                               │
│  商圈特征标签 │ 商品评分模型(XGBoost) │ 价格弹性 │ 投放策略模板   │
│  异常检测引擎 │ RAG知识库(Playbook)   │ 指标计算服务              │
└──────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────┐
│                     1. 数据层                                     │
│  SQLite(业务) │ ChromaDB(向量) │ Redis(队列/缓存) │ 模拟数据生成器│
└──────────────────────────────────────────────────────────────────┘
```

---

## 三、Agent 详细设计

### 3.0 Planner Agent（编排器）—— v2 新增

**作用**：运营不需要手动选择调用哪些 Agent，只需描述活动目标，Planner 自动拆解为任务清单。

**输入**：
- 活动目标类型：拉新 / 清库存 / 提GMV / 提ROI
- 活动范围：城市/商圈/门店
- 活动周期、预算、品类范围
- 运营约束：毛利下限、库存下限、品牌黑名单

**输出**：
- 任务清单（task_id × N）
- 任务执行顺序与依赖关系
- 每个 Agent 的输入参数
- 人审卡点位置
- 预期执行时间

**示例**：运营输入"北京望京商圈午间闪购，预算5000元，品类水果+饮料，目标提ROI"
→ Planner 输出：先跑选品Agent（品类范围=水果饮料，商圈=望京）→ 再跑定价Agent（毛利下限18%）→ 再跑文案+审核 → 人审卡点 → 上线

### 3.1 选品Agent

**输入**（按任务协议）：
- 商圈特征：LBS热力、写字楼/住宅占比、人均消费、竞品密度、时段活跃度
- 商品特征：历史销量、CTR/CVR、毛利率、库存周转、保质期、复购率
- 用户需求：搜索词趋势、加购趋势、新老客占比、价格敏感度
- 竞品特征：同品类价格带、折扣力度、活动频次
- 场景特征：天气、节假日、工作日/周末、午晚餐时段
- 约束条件：品类范围、毛利下限、库存下限

**特征工程（5类特征）**：

| 特征类别 | 特征项 | 来源 |
|----------|--------|------|
| 商圈特征 | LBS热力、写字楼/住宅比、消费水平、竞品密度、时段活跃度 | 模拟数据 |
| 用户需求变化 | 搜索趋势、加购趋势、新老客比、品类偏好、价格敏感度 | 模拟数据 |
| 商品特征 | 历史销量、CTR/CVR、毛利率、库存周转、保质期、复购率 | 模拟数据 |
| 竞品特征 | 同品类价格带、折扣力度、活动频次、排名位置 | 模拟数据 |
| 场景特征 | 天气、节假日、工作日/周末、午晚餐时段、大促节点 | 模拟数据 |

**输出**（统一规范）：
- `result`：选品清单（SKU × N），每个含推荐分、预期GMV、建议价格带
- `evidence`：关键特征重要性、历史相似活动参考
- `confidence`：模型预测概率
- `risk_level`：low/medium/high（库存风险+竞争风险+毛利风险综合）
- `recommended_action`：建议选品数量、Top-N推荐
- `need_human_review`：低置信度或高风险时=true

### 3.2 定价Agent —— v2 增强

**两层设计**：

第一层（规则层，硬约束）：
- 毛利下限保护：售价 < 成本×1.18 → 拦截
- 价格带约束：不超出商圈同品类价格带 P25-P75
- 爆款商品改价需二次确认
- 高波动价格（环比>30%）强制人工审批

第二层（智能层，LLM辅助）：
- 基于价格弹性估计推荐最优折扣深度
- 分商圈差异化定价
- 分时段调价策略（午市/晚市不同价）
- ROI预估

**输出**：建议售价、建议折扣、利润影响、ROI预估、风险说明

### 3.3 文案Agent

**多版本输出**：
- 商品标题（3个变体）
- 卖点文案（50字/100字）
- Banner文案
- Push文案
- 短信文案
- 不同渠道适配版本（搜索/推荐/社媒）

**能力增强**（v2）：
- RAG召回历史爆款文案模板
- 按商圈用户画像生成不同风格话术
- 审核检查点：违禁词、夸大宣传、品牌风险、价格一致性

### 3.4 审核Agent

**审核维度**：库存充足性、毛利达标、价格合规、文案合规、历史黑名单命中、竞品策略冲突

**输出**：通过/需修改/拒绝 + 问题项清单 + 修复建议

### 3.5 诊断Agent —— v2 漏斗分层版

**核心改进**：不笼统归因，先通过规则+时序模型定位到具体漏斗层级，再让 LLM 做归因解释。

| 异常层级 | 检测方法 | 归因方向（LLM推理） |
|----------|----------|---------------------|
| 曝光下滑 | EWMA/Z-score | 预算不足/渠道分发异常/排位下降/大盘流量波动 |
| CTR下滑 | EWMA/Z-score | 文案吸引力不足/图片素材问题/价格不具竞争力/人群错配 |
| 加购率下滑 | EWMA/Z-score | 商品卖点弱/评价差/价格敏感/竞品打折 |
| 支付转化下滑 | EWMA/Z-score | 库存不足/优惠券异常/结算链路问题/配送时效问题 |

**检测方法**：阈值规则 + 环比/同比波动 + EWMA + CUSUM
**归因方法**：规则树定位层级 → 指标贡献度分析 → 历史案例匹配 → LLM生成解释和建议

### 3.6 复盘Agent —— v2 新增

**作用**：活动结束后自动生成复盘报告，沉淀可复用策略到 Playbook 库。

**输出**：
- 本次活动效果概览（GMV/ROI/动销率 vs 目标）
- 与历史同类活动对比
- 成功SKU / 失败SKU 分析
- 流量→转化→利润归因分解
- 可复用策略（存入 Playbook）
- 下次活动优化建议

---

## 四、关键协议设计

### 4.1 标准化任务协议（TaskProtocol JSON）

所有 Agent 间通信统一格式：

```json
{
  "task_id": "task_001",
  "workflow_id": "wf_20250507_001",
  "agent_name": "selection_agent",
  "biz_goal": "提升ROI",
  "input": {
    "city": "北京",
    "district": "望京",
    "campaign_type": "午间闪购",
    "budget": 5000,
    "category_scope": ["水果", "饮料"]
  },
  "constraints": {
    "gross_margin_floor": 0.18,
    "inventory_min": 50
  },
  "output_schema": ["sku_id", "recommend_score", "expected_gmv", "risk_level", "reason"],
  "confidence_threshold": 0.7,
  "human_review_required": true
}
```

### 4.2 Agent 统一输出规范

每个 Agent 输出必须包含 6 个字段：

```python
class AgentOutput:
    result: Any              # 核心结果（类型按Agent不同）
    evidence: List[str]      # 决策依据（数据来源、规则引用）
    confidence: float        # 置信度 0-1
    risk_level: str          # low / medium / high
    recommended_action: str  # 给运营的建议
    need_human_review: bool  # 是否需要人工审核
```

---

## 五、反馈闭环设计 —— v2 新增

运营对每个 Agent 输出可以：
- **采纳**：记录采纳，追踪后续实际效果（GMV/ROI）
- **拒绝+原因**：记录拒绝原因，用于优化 prompt 和模型

数据表设计：
```sql
agent_feedback (
  id, run_id, agent_name, task_id,
  accepted BOOLEAN, reject_reason TEXT,
  actual_gmv FLOAT, actual_roi FLOAT,
  created_at
)
```

定期分析采纳率、拒绝原因分布，驱动迭代。

---

## 六、三阶段开发排期

### Phase 0：基础设施 + 数据底座（Week 1，~5天）

| 任务 | 产出 | 工期 |
|------|------|------|
| 项目脚手架 | FastAPI + LangGraph + Celery 初始化 | 1天 |
| 数据库设计 | SQLite建表 + ChromaDB初始化 + agent_feedback表 | 0.5天 |
| 模拟数据生成器 | 5商圈×200商品×10000用户×3月数据，含噪音 | 2天 |
| 指标口径文档 | 12个北极星指标 + 6个护栏指标统一定义 | 0.5天 |
| 漏斗看板 V1 | Streamlit + Plotly 基础漏斗图 + 指标卡 | 1天 |

**Phase 0 里程碑**：模拟数据跑通，漏斗看板可展示，指标口径统一。

### Phase 1：MVP 核心工作流（Week 2-4，~13天）

| 任务 | 产出 | 工期 |
|------|------|------|
| 任务协议 + Agent基类 | TaskProtocol定义 + BaseAgent + AgentOutput | 1天 |
| Planner Agent | 活动目标→任务清单拆解 + LangGraph编排入口 | 2天 |
| 商圈特征工程 | 5类特征计算 + FeatureExtractor基类 | 2天 |
| 选品Agent（规则引擎版） | 规则评分卡 + 工具调用 + 统一输出 | 2天 |
| 文案Agent | Prompt模板 + RAG爆款召回 + 多版本输出 | 1.5天 |
| 审核Agent | 审核规则 + 人机卡点1（选品审核） | 1天 |
| 定价Agent（规则层） | 毛利红线 + 价格带约束 + 人机卡点2（终审） | 1.5天 |
| 工作流编排+前端操作台 | LangGraph全流程 + Streamlit交互 | 2天 |

**Phase 1 里程碑**：运营可创建活动→Planner拆解任务→选品→定价→文案→审核（含2个人审卡点），端到端跑通。

### Phase 2：智能增强（Week 5-7，~13天）

| 任务 | 产出 | 工期 |
|------|------|------|
| 选品推荐模型 | LightGBM排序 + 特征重要性分析 | 2天 |
| 定价Agent（智能层） | 价格弹性估计 + ROI预估 | 2天 |
| RAG知识库 | 历史活动案例 + 爆款文案 + Playbook检索 | 2天 |
| 异常检测引擎 | Z-score + EWMA + CUSUM 异动检测 | 2天 |
| 诊断Agent V1 | 漏斗分层归因 + LLM解释生成 | 2天 |
| 人工审核台增强 | 审核面板 + agent_feedback采集 | 1天 |
| 诊断看板 | 告警中心 + 归因展示 | 2天 |

**Phase 2 里程碑**：选品推荐从规则引擎升级为模型排序，诊断Agent可自动识别异常并分层归因，运营可反馈Agent质量。

### Phase 3：闭环运营（Week 8-10，~13天）

| 任务 | 产出 | 工期 |
|------|------|------|
| 复盘Agent | 活动报告生成 + 历史对比 + 策略提取 | 2天 |
| Playbook库 | 可复用策略模板的存储和检索 | 1.5天 |
| AB实验框架 | 分流逻辑 + 指标对比 + 显著性检验 + 结果归档 | 3天 |
| Prompt模板管理 | 版本管理 + A/B对比 | 1.5天 |
| Agent执行回放 | 审计日志 + 执行轨迹可视化 | 2天 |
| 投放策略引擎 | 预算分配 + 渠道组合 + 调价规则 | 2天 |
| 端到端联调+打磨 | 全流程测试 + Bug修复 + 演示准备 | 1天 |

**Phase 3 里程碑**：形成"执行→监控→诊断→复盘→沉淀"闭环，AB实验可验证 AI 策略效果。

---

## 七、项目目录结构（v2更新）

```
flash-sale-ai/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── workflow.py          # 工作流API（创建/审批/恢复）
│   │   │   ├── selection.py         # 选品API
│   │   │   ├── diagnosis.py         # 诊断API
│   │   │   ├── dashboard.py         # 看板数据API
│   │   │   ├── review.py            # 复盘API
│   │   │   └── feedback.py          # 运营反馈API
│   │   ├── agents/
│   │   │   ├── base.py              # Agent基类 + AgentOutput + TaskProtocol
│   │   │   ├── planner_agent.py     # 编排器Agent
│   │   │   ├── selection_agent.py   # 选品Agent
│   │   │   ├── pricing_agent.py     # 定价Agent（规则层+智能层）
│   │   │   ├── copywriting_agent.py # 文案Agent
│   │   │   ├── review_agent.py      # 审核Agent
│   │   │   ├── diagnosis_agent.py   # 诊断Agent（漏斗分层归因）
│   │   │   ├── review_report_agent.py # 复盘Agent
│   │   │   └── prompts/             # Prompt模板目录
│   │   │       ├── planner.md
│   │   │       ├── selection.md
│   │   │       ├── pricing.md
│   │   │       ├── copywriting.md
│   │   │       ├── review.md
│   │   │       ├── diagnosis.md
│   │   │       └── review_report.md
│   │   ├── workflow/
│   │   │   ├── graph.py             # LangGraph StateGraph定义
│   │   │   ├── state.py             # CampaignState Schema
│   │   │   └── checkpoints.py       # 人机卡点 + 恢复逻辑
│   │   ├── models/
│   │   │   ├── product_scoring.py   # 商品评分模型(LightGBM)
│   │   │   ├── feature_engine.py    # 5类特征工程
│   │   │   ├── anomaly_detector.py  # 异动检测（Z-score/EWMA/CUSUM）
│   │   │   └── price_elasticity.py  # 价格弹性估计
│   │   ├── services/
│   │   │   ├── data_service.py      # 数据服务（抽象基类）
│   │   │   ├── metrics_service.py   # 指标计算（唯一入口）
│   │   │   ├── ab_test.py           # AB实验框架
│   │   │   ├── ad_strategy.py       # 投放策略引擎
│   │   │   ├── rag_service.py       # RAG知识库检索
│   │   │   └── playbook_service.py  # Playbook存储/检索
│   │   ├── db/
│   │   │   ├── models.py            # SQLAlchemy模型（含agent_feedback）
│   │   │   └── vector_store.py      # ChromaDB操作
│   │   ├── tasks/
│   │   │   └── celery_tasks.py      # 定时诊断任务
│   │   └── core/
│   │       ├── config.py            # 配置
│   │       ├── mock_data.py         # 模拟数据生成（含噪音）
│   │       └── metrics_definition.md # 指标口径文档
│   ├── requirements.txt
│   └── main.py
├── frontend/
│   ├── app.py                       # Streamlit主入口
│   ├── pages/
│   │   ├── 1_workflow.py            # 活动工作流操作台
│   │   ├── 2_selection.py           # 选品推荐页
│   │   ├── 3_copywriting.py         # 文案生成+审核页
│   │   ├── 4_diagnosis.py           # 诊断面板+告警中心
│   │   ├── 5_dashboard.py           # 漏斗看板+北极星指标
│   │   ├── 6_review.py              # 复盘中心
│   │   └── 7_playbook.py            # Playbook库
│   └── components/
│       ├── funnel_chart.py          # 漏斗图
│       ├── metric_card.py           # 指标卡
│       ├── approval_panel.py        # 审核面板
│       ├── agent_output_card.py     # Agent输出卡片（统一渲染）
│       └── feedback_widget.py       # 采纳/拒绝反馈组件
├── notebooks/
│   ├── feature_engineering.ipynb
│   └── model_training.ipynb
└── README.md
```

---

## 八、AB实验框架

### 优先实验列表

1. AI选品 vs 人工选品 → 核心指标：GMV、动销率波动
2. AI定价 vs 固定折扣 → 核心指标：ROI、毛利率
3. AI文案 vs 人工文案 → 核心指标：CTR
4. Agent工作流 vs 传统Excel流程 → 核心指标：活动筹备时长、人均管理活动数

### 实验平台能力

- 按商圈+用户ID哈希分流
- 实验配置管理（实验组/对照组比例）
- 指标实时追踪（GMV uplift、ROI uplift、CTR、CVR）
- 双样本T检验 + Bootstrap置信区间
- 实验结果归档（可回溯）

---

## 九、时间线总览

```
Week 1  ████████ Phase 0: 基础设施 + 数据底座 + 漏斗看板V1
Week 2  ████████ Phase 1: Planner + 选品(规则引擎) + 文案
Week 3  ████████ Phase 1: 审核 + 定价(规则层) + 人机卡点
Week 4  ████████ Phase 1: 工作流编排 + 前端操作台联调
        ───────── Phase 1 里程碑：端到端工作流跑通 ─────────
Week 5  ████████ Phase 2: 选品模型(LightGBM) + 定价智能层
Week 6  ████████ Phase 2: RAG知识库 + 异常检测引擎
Week 7  ████████ Phase 2: 诊断Agent + 审核台增强 + 反馈采集
        ───────── Phase 2 里程碑：智能推荐+诊断可用 ─────────
Week 8  ████████ Phase 3: 复盘Agent + Playbook库
Week 9  ████████ Phase 3: AB实验框架 + Prompt模板管理
Week 10 ████████ Phase 3: 投放策略 + Agent回放 + 联调打磨
        ───────── Phase 3 里程碑：完整闭环交付 ─────────
```

---

## 十、下一步行动

1. 确认 v2 方案 → 启动 Phase 0
2. Phase 0 第一个动作：搭建 FastAPI 脚手架 + 指标口径文档
3. 模拟数据生成器优先于任何 Agent 开发（无数据无法验证）

---

*计划版本：MVP v2.0*
*制定时间：2026-05-07*
*参考来源：GPT-5.5 方案碰撞 + 原始简历需求*
