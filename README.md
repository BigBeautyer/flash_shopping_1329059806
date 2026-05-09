# ⚡ AI 闪购运营中台

Multi-Agent 闪购运营平台 — 选品 → 定价 → 文案 → 审核（人机交互）→ 监控 → 诊断 → 复盘

**技术栈**: FastAPI + LangGraph + Streamlit + SQLite · LLM: DeepSeek API

---

## 快速启动

### 1. 后端

```bash
cd backend
本机:
cd /Users/heguoliang/Documents/ai_projects/Lighting_shopping/backend
uvicorn main:app --reload --port 8000

# 安装依赖（首次）
pip install -r requirements.txt --break-system-packages

# 生成模拟数据（首次或需要重置数据时）
python -c "from app.core.mock_data import generate_all; generate_all(force=True)"

# 启动 API 服务
python main.py
```

后端默认运行在 `http://127.0.0.1:8000`

### 2. 前端

```bash
cd frontend
本机:
cd /Users/heguoliang/Documents/ai_projects/Lighting_shopping/frontend
streamlit run app.py

# 安装依赖（首次）
pip install streamlit plotly httpx pandas --break-system-packages

# 启动
streamlit run app.py
```

前端默认运行在 `http://localhost:8501`

### 3. 验证

- 浏览器打开 `http://localhost:8501`
- 左侧导航可见 6 个页面：**数据看板** | **工作流操作台** | **诊断面板** | **活动复盘** | **AB 实验** | **执行回放**
- API 文档：`http://127.0.0.1:8000/docs`

---

## 项目结构

```
Lighting_shopping/
├── backend/
│   ├── main.py                  # FastAPI 入口
│   ├── app/
│   │   ├── agents/              # 7 个 Agent（Planner/选品/定价/文案/审核/诊断/复盘）
│   │   │   └── prompts/         # Agent Prompt 模板
│   │   ├── api/                 # API 路由（dashboard/workflow/diagnosis/rag/phase3）
│   │   ├── core/                # 配置、模拟数据生成
│   │   ├── db/                  # SQLAlchemy 模型
│   │   ├── models/              # 特征工程、LightGBM 评分模型
│   │   ├── services/            # 指标/数据/异常检测/漏斗归因/AB实验/RAG知识库
│   │   └── workflow/            # LangGraph 工作流定义 + 状态
│   ├── data/                    # SQLite 数据库文件
│   └── requirements.txt
├── frontend/
│   ├── app.py                   # Streamlit 入口
│   └── pages/
│       ├── dashboard.py         # 漏斗看板
│       ├── workflow.py          # 工作流操作台
│       ├── diagnosis.py         # 诊断面板
│       ├── postmortem.py        # 活动复盘
│       ├── ab_experiment.py     # AB 实验
│       └── replay.py            # Agent 执行回放
├── agents.md                    # 架构决策 + 经验沉淀
└── README.md
```

---

## Agent 工作流

```
创建活动 → Planner → 选品Agent → [人审卡点] → 定价Agent → 文案Agent
                                                    → 审核Agent → [终审卡点] → 上线
```

人审卡点：运营在前端逐商品打勾审批，可部分通过/全部驳回。

---

## API 概览

| 模块 | 端点 | 说明 |
|------|------|------|
| 看板 | `GET /api/dashboard/snapshot` | 漏斗快照 |
| 看板 | `GET /api/dashboard/trend` | GMV 趋势 |
| 工作流 | `POST /api/workflow/campaigns` | 创建活动 |
| 工作流 | `GET /api/workflow/campaigns/{id}/state` | 查询状态 |
| 工作流 | `POST /api/workflow/campaigns/{id}/approve-selection` | 选品审批 |
| 诊断 | `POST /api/diagnosis/run` | 运行诊断 |
| 诊断 | `GET /api/diagnosis/anomalies` | 异动检测（快速） |
| 复盘 | `POST /api/phase3/postmortem/run` | 运行复盘 |
| 复盘 | `GET /api/phase3/postmortem/playbooks` | 历史 Playbook |
| AB实验 | `POST /api/phase3/ab/run` | 运行 AB 实验 |
| AB实验 | `POST /api/phase3/ab/compare` | 多商圈对比矩阵 |
| 回放 | `GET /api/phase3/replay/runs` | 执行日志列表 |
| 回放 | `GET /api/phase3/replay/timeline` | 决策时间线 |

完整文档：`http://127.0.0.1:8000/docs`

---

## 当前进度

| 阶段 | 状态 |
|------|------|
| Phase 0 · 基础设施 + 数据看板 | ✅ 完成 |
| Phase 1 · MVP 核心工作流 | ✅ 完成 |
| Phase 2 · 诊断 Agent | ✅ 完成 |
| Phase 2 · LightGBM 模型 + RAG 知识库 | ✅ 完成 |
| Phase 3 · 复盘 + AB实验 + 闭环（全阶段完成） | ✅ 完成 |
