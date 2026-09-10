# HARA 案例库 v2 匹配合同

## 1. 目的

案例库是跨项目复用的语义参考资产，不是某一个项目 HARA Excel 的复制品。

运行时必须遵守：

> **案例库匹配使用功能语义、失效语义、危害语义和场景语义；不得使用项目级 ID、功能排序或 Excel 行号。**

本合同对应：

- schema：`references/case_library/schema_v2.json`
- 运行时匹配器：`scripts/utils/case_library.py`
- 输入侧域/语义解析：`scripts/utils/data_models.py`、`scripts/utils/docx_parser.py`
- S4 预填入口：`scripts/stages/stage_hara.py`、`scripts/stages/stage_hara_simple.py`

## 2. 明确禁止作为运行时匹配键的字段

以下字段不得出现在案例库 v2 的正式运行时 schema 中，也不得被 `CaseLibrary` 用作匹配条件：

- `func_id`
- `analysis_unit_id`
- `failure_id`
- 项目自定义功能编号（例如 `F16`）
- 项目内功能排序号
- Excel 行号或 HAZOP 行号
- 来源项目的功能名称全文相等判断

旧资产中的这些字段如需审计，只能保留在离线迁移报告中，不得回流到案例库运行时资产。

`case_id` 可以保留。它是案例资产自身的稳定引用 ID，不用于推断当前项目功能或参与新项目的语义搜索。

例外不是“按 ID 匹配”，而是**受控的下游来源解引用**：如果 S3 已由同一运行时案例资产选择案例并保存 `source="case_library" + case_ids`，S4 必须用这些 ID 取回同一批案例，同时重新校验 domain/function/failure/anomaly/hazard 语义。ID 缺失、资产变化或语义不一致时必须失败关闭，不得改做 similar 后交给 Agent 猜测。

## 3. 输入侧与案例库侧的边界

### 3.1 输入侧可以保留原始 ID

`intermediate.json`、`s1_decisions.json`、`s2_decisions.json`、`s3_hazop.json` 可以保留当前项目的原始功能 ID，例如 `F16`，以便进行项目链路追溯。

但是输入侧的原始 ID 必须与以下字段分开：

```json
{
  "source_func_id": "F16",
  "domain": "PT",
  "function_profile": {
    "canonical_function_family": "pt_traction_torque_control"
  }
}
```

其中：

- `source_func_id`：仅追溯，不匹配；
- `domain`：canonical domain；
- `function_profile`：跨项目匹配的唯一功能语义入口。

### 3.2 域不能由 func_id 单独推断

当前 `extract_domain()` 对 `P_func_0001` 有效，但对 `F16` 会返回 `unknown`。新规则是：

1. 优先使用显式项目域或文档模板域；
2. 其次使用 Domain Pack 的功能语义匹配；
3. 最后才允许使用 ID 前缀作为兼容性提示；
4. 无法确定时必须显式报错，不得静默生成 `unknown` 后继续生产 S4。

输入侧别名必须先 canonicalize：

```text
P / POWERTRAIN / PT -> PT
A / ADAS / AD       -> AD
B / BODY / BD       -> BD
Info / ET           -> ET
```

案例库 v2 只存 canonical domain。

## 4. 语义匹配键

### 4.1 功能语义键

由 Domain Pack 和输入文档的功能/子功能语义共同生成：

```text
canonical_function_family
+ sorted semantic_roles
+ sorted controlled_objects
```

功能名称全文只能用于生成 profile，不能作为唯一等值键。

### 4.2 失效语义键

```text
canonical_mode
+ anomaly_class
```

`canonical_mode` 使用 S2 的标准失效模式枚举。旧数据中的“过大/过小”等标签必须在迁移阶段映射或标记待审核，不能直接当作新的项目匹配键。

`anomaly_class` 必须通过异常规范化和别名表生成，例如：

```text
提供要求的驱动扭矩功能丢失
-> required_drive_torque_not_provided
```

### 4.3 危害语义键

```text
hazard_family
+ vehicle_hazard_class
```

危害原始文本保留用于最终输出，但不能只靠全文相等匹配。

### 4.4 场景语义键

场景必须被拆解为稳定维度，例如：

```text
maneuver
road_type
speed_band
traffic_condition
following_distance
slope
environment
vulnerable_road_user
```

由规范化维度生成确定性 `semantic_fingerprint`。来源 Excel 的场景 ID、项目行号不作为跨项目匹配键。

## 5. 匹配等级

### 5.1 exact

必须同时满足：

- canonical domain 一致；
- function profile 达到 exact；
- canonical failure mode 一致；
- anomaly class 一致；
- hazard profile 一致；
- scenario semantic fingerprint 一致；
- 案例 assessment 字段完整且符合当前规则。

输出：

```json
{
  "prefill_source": "exact",
  "prefill_locked": true
}
```

同一完整语义键若命中多条案例，只有 assessment（S/E/C、理由、ASIL、SG、安全状态、FTTI）一致时才允许 exact 锁定。只要 assessment 存在冲突，就必须降级为 `similar` 候选并保持可编辑，禁止按 `case_id` 排序后静默选择第一条。当前 PT 迁移审计发现 50 个重复语义组，其中 46 个组存在 assessment 冲突；详见 `PT_migration_report.json.semantic_collision_audit`。


### 5.1.1 S3 来源案例 exact 交接

当 S3 controlled draft 已保留 `case_ids` 时，场景并非“缺失”，而是包含在这些已选择案例中。S4 必须：

1. 解引用全部 `case_ids`，不能只取排序第一条；
2. 复核功能族、失效模式、异常类和危害类仍与 S3 一致；
3. 展开每条案例的 `event_description`、场景、S/E/C及理由、SG、安全状态和 FTTI；
4. 输出 `prefill_source="case_library_exact"`、`source_case_locked=true`、`agent_action="locked"`；
5. validate 时重新解引用并逐字段比对，禁止 Agent 改写；
6. 若同一 source failure 组的 SG/安全状态/FTTI 只在一行填写，仅当组内非空值唯一时允许确定性展开；有冲突则失败关闭。

该路径不违反“项目 ID 不作为语义匹配键”：`case_ids` 只引用上游已经选定的案例资产，不能用于给新功能寻找案例。

### 5.2 similar

功能、失效和异常语义高度一致，但场景不同或场景尚未确定。

输出候选集，不得直接把第一条候选当成正式事件：

```json
{
  "prefill_source": "similar",
  "prefill_locked": false,
  "prefill_candidates": []
}
```

### 5.3 template

仅功能族和失效模式一致。

只允许提供：

- 安全目标模板；
- 安全状态参考；
- FTTI 参考；
- 相关案例和分析提示。

不得伪造正式 S/E/C 结论。

### 5.4 none

没有足够可靠的语义证据。生成 Agent 任务，不套用其他域或旧文本表。

## 6. 场景缺失规则

如果 S3 尚未提供场景语义，运行时不得判定 exact。

正确流程是：

```text
S3 功能/失效/异常/危害
  -> 生成 similar/template 候选
  -> Agent 选择或补充场景
  -> 生成 scenario semantic fingerprint
  -> 重新匹配
  -> 满足完整条件时才提升为 exact
```

这条规则用于避免把 `scenario=""` 错误地传入 exact 匹配器。

## 7. 候选排序规则

`match_cases()` 必须返回候选列表，而不是只返回第一条记录。

建议排序顺序：

1. canonical function family exact；
2. semantic roles 重叠数；
3. canonical failure mode exact；
4. anomaly class exact；
5. hazard profile exact；
6. scenario dimension 重叠数；
7. 案例资产完整度；
8. provenance 质量。

排序结果必须可解释，并在输出中记录命中的字段：

```json
{
  "matched_by": [
    "domain",
    "canonical_function_family",
    "canonical_failure_mode",
    "anomaly_class"
  ],
  "confidence": 0.82
}
```

## 8. S4 输出要求

预填逻辑必须和正式 S4 验证结构兼容。每个生成的 event 必须能够建立：

- `analysis_unit_id`（由当前项目/Domain Pack 生成，不来自案例库匹配）；
- `failure_id`（由当前 S3 生成，不来自案例库）；
- `vehicle_hazard_id`；
- `hazard_family`；
- `semantic_key`；
- `mapping_status`；
- `mapping_source`。

案例库只提供参考语义和 assessment，不得把来源项目的 ID 直接复制到当前项目。

## 9. 迁移验收标准

PT v2 资产迁移完成后必须满足：

- 437 条旧案例可逐条加载或明确标记迁移失败；
- 正式运行资产中不存在 `func_id`；
- 正式运行资产中不存在依赖来源项目的 `analysis_unit_id`/`failure_id` 匹配；
- 每条案例有 canonical function profile；
- 每条案例有 canonical failure profile；
- 每条案例有 hazard profile；
- 每条案例有 scenario semantic fingerprint，或显式标记场景缺失；
- 迁移报告记录所有无法自动映射的案例；
- 运行时加载器只读取 v2 资产，不读取旧案例库作为隐式 fallback。

## 10. 版本策略

- 当前旧 `PT_cases.json` 保留为迁移输入和回滚参考；
- 新资产使用明确的 v2 文件名；
- v2 已通过真实数据组4/5回归；旧资产仅保留为离线迁移/审计参考，运行时不得读取；
- 运行时不得同时混用 v1 和 v2 的匹配结果。
