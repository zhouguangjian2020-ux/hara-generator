# S5 整车安全目标汇总规则

本文档描述 S5 阶段如何从 S4 HARA 分析结果中提取、去重、编号整车级安全目标（Safety Goal），并写入 Excel"整车安全目标"sheet。

## 1. 总体流程

```
s4_hara_final.json
    │
    ├─► 提取：筛选有安全目标文本的非QM事件
    ├─► 去重：按受控工程语义（域、危害族、规范安全目标、安全状态、FTTI）合并
    ├─► 聚合：ASIL取最高，功能/异常去重合并
    └─► 编号：{域}_SG_VH_{4位流水}，按ASIL从高到低排序，同ASIL按HARA表首次出现顺序
            │
            └─► safety_goals.json ──► Excel"整车安全目标"sheet
                   （同时需要 s4_hara_final.json 提供事件级明细）
```

## 2. 提取规则

从 `s4_hara_final.json` 的 `hazards[].events[]` 中筛选事件，必须同时满足：

1. 所属 hazard 非 `skip`
2. event 有 `safety_goal` 文本（非空、非 None）
3. event 的 `asil` 非 QM 且非 None（S=0 或 C=0 的事件不生成安全目标）

## 3. 去重规则

使用以下受控语义键去重：

```text
domain + hazard_family + canonical_safety_goal + safe_state + ftti
```

- `canonical_safety_goal` 优先取 Domain Pack 批准的规范安全目标；未沉淀的历史数据回退为事件 `safety_goal` 原文；
- `hazard_family`、安全状态或 FTTI 不同，必须拆分为不同整车安全目标；
- **ASIL 不属于去重键**：同一安全目标在不同事件中可能取得 A/B/C/D，S5 必须合并并取其中最高 ASIL（D > C > B > A），而不是按 ASIL 膨胀成多条整车级 SG；
- 功能列表、异常列表、`hazard_id`、`vehicle_hazard_id` 和事件级安全目标 ID 均去重合并，并在 `event_links` 中保留每条事件的完整追溯；
- 只有 Pack 显式提供规范安全目标时，才允许跨轻微文案差异合并；不能依靠文本相似度猜测安全目标等价性。

## 4. 编号规则

整车级连续编号，格式 `{域}_SG_VH_{4位流水号}`，如 `P_SG_VH_0001`、`A_SG_VH_0001`。

- 域前缀取自 `s4_hara_final.json` 的 `domain` 字段（P/A/B/CB/CS/Info）
- `VH` = Vehicle Hazard（整车级）
- 4 位流水号从 0001 开始

排序优先级：
1. ASIL 从高到低：D → C → B → A
2. 同 ASIL 按在 HARA 表中首次出现顺序（即去重时的自然顺序，不按文本字典序）

## 5. Excel Sheet 列定义

Sheet 名称：**整车安全目标**（模板已有此 sheet，Row1-3 为合并大标题，Row4 为表头，数据从 Row5 开始）

模板采用**双区结构**：左区为事件级明细，右区为整车级汇总。

### 左区 A-E（事件级明细，每个非QM危害事件一行）

| 列 | 字段 | 说明 |
|----|------|------|
| A | 安全目标ID | 事件级 ID，如 P_SG_01001（来自 S4） |
| B | 安全目标 | 事件级安全目标文本 |
| C | ASIL | 事件级 ASIL 等级 |
| D | 安全状态 | 事件级安全状态 |
| E | FTTI | 事件级 FTTI |

### F 列

| 列 | 字段 | 说明 |
|----|------|------|
| F | 序号 | 每个 SG 组的序号（1, 2, 3…），写在组首行，组内纵向合并 |

### 右区 G-L（整车级汇总，每个唯一 SG 只在首行填写）

| 列 | 字段 | 说明 |
|----|------|------|
| G | 整车安全目标ID | 整车级编号，如 P_SG_VH_0001 |
| H | 安全目标合并 | 去重后的整车级安全目标文本 |
| I | ASIL | 取所有关联事件的最高 ASIL |
| J | Safe State | 整车级安全状态 |
| K | FTTI | 整车级 FTTI |
| L | 备注 | 留空 |

### 排列规则

- 事件按整车级 SG 分组，同一 SG 的事件连续排列
- 组间顺序按 SG 编号顺序（ASIL 降序，同 ASIL 按 HARA 表首次出现顺序）
- 整车级汇总（G-L）写在每个 SG 组的**首行**，后续同组事件的 G-L 留空

### 合并单元格规则

每个 SG 组的数据行写完后，以下列按组纵向合并（与参考 Excel 格式一致）：
- **F 列**（序号）：组合并，显示组序号
- **G-K 列**（整车安全目标ID、安全目标合并、ASIL、Safe State、FTTI）：组合并
- **L 列**（备注）：不合并
- A-E 列（事件级明细）：不合并，每行独立

## 6. 与 S4 事件级 SG ID 的关系

S4 `validate` 阶段的 `assign_safety_goal_ids()` 为**每个非QM事件**分配独立的事件级 ID（如 `P_SG_01001`），写入 HARA 分析表的 O 列。

S5 阶段的整车级编号（`P_SG_VH_0001`）是**更高层级的汇总**，不修改 S4 的输出，不回填 HARA 表。两者并存：
- 事件级 ID：HARA 分析表中每行一个，用于追溯
- 整车级编号：整车安全目标 sheet 右区中每条一个，用于安全目标清单

## 7. 写入依赖

写入 Excel 整车安全目标 sheet **必须同时提供**：
- `--s4 s4_hara_final.json`：提供左区 A-E 列的事件级明细
- `--s5 safety_goals.json`：提供右区 G-L 列的整车级汇总

仅传 `--s5` 不传 `--s4` 会被拒绝并报错。

## 8. 特殊情况

- **ET域（座舱）**：功能多为 QM 级，通常无安全目标，S5 输出可能为空
- **同一 SG 跨域**：理论上安全目标应在同一域内，若出现跨域合并，域字段取第一个
- **安全状态/FTTI 为空**：部分 SG 可能没有预填安全状态或 FTTI，对应列留空

## 9. 2026-08-25：ASIL 聚合防膨胀规则

整车级安全目标表达的是同一危害控制目标，不应因关联事件的风险等级不同而重复编号。例如，同一域、同一危害族、同一规范安全目标、同一 Safe State 和同一 FTTI 的事件分别为 ASIL A、B、C 时，S5 应输出 **1 条**整车级安全目标，ASIL 为 **C**，并保留 3 条事件追溯。

不得以“减少条目数”为目的跨危害族、跨 Safe State 或跨 FTTI 合并；若这些工程约束不同，即使安全目标文案相同，也必须保持独立。

## 10. 质量与发布闸门（2026-08-26）

`sg generate` 在写出 `safety_goals.json` 前执行质量校验：

- Agent 新建/可编辑事件的 SG 描述过短、必要工程语义不完整会返回 error 并阻断写文件；
- `risk_matrix_locked=true` 或 `source_case_locked=true` 的权威 exact 安全目标已经在 S4 逐字段验证，S5 不得再用“至少 10 个汉字”等自由文本启发式否决，也不得要求 Agent 改写；
- 失败结果不得覆盖已有有效 `safety_goals.json`；
- 若 S5 失败：未锁定事件回到 `s4_hara_agent.json` 修正；locked 事件必须检查案例资产、来源映射或校验代码，禁止直接改写权威 SG；之后重新执行 `hara validate --s3 -> sg generate`；
- 禁止直接修改 `s4_hara_final.json` 或 `safety_goals.json` 来绕过阶段追溯；
- `write --s5` 会复核 S5 的 `validation_issues`，含 error 的旧/stale 文件会被拒绝，`--force` 不能绕过。

warning（例如 SG 与 anomaly 的关键词关联性较弱）可以保留进入正式输出，但必须在工程审核中可见并可追溯。
