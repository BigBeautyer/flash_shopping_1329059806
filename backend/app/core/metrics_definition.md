# 闪购业务 — 指标口径文档

> 唯一指标计算入口：`app/services/metrics_service.py`
> 禁止各 Agent 自行计算指标，必须通过 MetricsService。

---

## 一、12 个北极星指标

| # | 指标 | 英文 | 计算口径 | 数据表 | 异常阈值 |
|---|------|------|----------|--------|----------|
| 1 | 曝光量 | Exposure UV | COUNT(DISTINCT user_id) WHERE funnel_stage='exposure' | orders | 环比波动>30% |
| 2 | 点击率 | CTR | 点击UV / 曝光UV | orders | 环比下降>20% |
| 3 | 加购率 | Cart Rate | 加购UV / 点击UV | orders | 环比下降>15% |
| 4 | 支付转化率 | CVR (pay/cart) | 支付UV / 加购UV | orders | 环比下降>10% |
| 5 | 客单价 | AOV | SUM(price × quantity) / COUNT(payment orders) | orders | 环比偏移>20% |
| 6 | GMV | GMV | SUM(price × quantity) WHERE funnel_stage='payment' | orders | 环比下降>25% |
| 7 | ROI | ROI | GMV / 投放成本（campaign.budget） | orders + campaign | < 1.5 |
| 8 | 毛利率 | Gross Margin | 1 - (SUM(cost_price × qty) / SUM(price × qty)) | orders + product | < 15% |
| 9 | 动销率 | Sell-through Rate | 有销售SKU数 / 总选品SKU数 | orders + campaign_task | < 60% |
| 10 | 复购率 | Repurchase Rate | 30日内复购用户 / 总支付用户 | orders | < 8% |
| 11 | 活动筹备时长 | Campaign Prep Time | campaign.created_at → campaign.status='running' 的时间差 | campaign | > 48h |
| 12 | 人均管理活动数 | Campaigns per Operator | 同时间运行活动数 / 运营人数 | campaign | < 3 |

---

## 二、6 个护栏指标

| # | 指标 | 计算口径 | 告警阈值 |
|---|------|----------|----------|
| 1 | 缺货率 | 库存=0的SKU / 总选品SKU | > 10% |
| 2 | 库存周转天数 | SKU平均库存 / 日均销量 | > 30天 |
| 3 | 退款率 | 退款订单 / 支付订单 | > 5% |
| 4 | 客诉率 | 客诉订单 / 支付订单 | > 2% |
| 5 | 文案审核驳回率 | 驳回任务 / 总审核任务 | > 30% |
| 6 | AI建议采纳率 | 采纳的Agent输出 / 总Agent输出 | < 60% |

---

## 三、漏斗定义

```
曝光(exposure) → 点击(click) → 加购(cart) → 支付(payment) → 复购(repurchase)
     ↓               ↓              ↓             ↓              ↓
  曝光UV          点击UV         加购UV        支付UV         复购UV
                    CTR         加购率       转化率CVR      复购率
                 (点击/曝光)  (加购/点击)   (支付/加购)   (复购/支付)
```

---

## 四、时间粒度

| 粒度 | 用途 | 示例 |
|------|------|------|
| 小时 | 实时监控、异常告警 | 午间闪购11:00-14:00 |
| 天 | 日常看板、AB实验 | 5月7日漏斗对比 |
| 周 | 周复盘、趋势分析 | 第19周GMV趋势 |
| 月 | 月度迭代、策略评估 | 4月ROI报告 |

---

*最后更新：2026-05-07*
*所有指标计算走 `MetricsService`，禁止硬编码口径*
