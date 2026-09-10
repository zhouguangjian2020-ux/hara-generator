# Agent 推理工作流指引

本文档描述调用本 Skill 的 Agent 如何执行 HARA 分析的推理部分。

**重要：最终产物必须包含 5 个数据 Sheet（相关项功能清单 + 失效模式选择 + HAZOP 分析 + HARA 分析 + 整车安全目标），S3 HAZOP、S4 HARA 和 S5 整车安全目标均为强制步骤，不可跳过。**

## 前置

PT 域运行时权威资产位于 `references/domain_packs/PT/PT_subfunctions.json`。原始 `PT_子功能.XLSX` 只用于离线构建该 JSON，Agent 运行时不得要求上传或读取该 XLSX。

已通过阶段一执行 `run_hara.py parse` 获得 `intermediate.json`。输入可以是标准
`.docx`，也可以是旧 `.doc`；旧 DOC 的转换器和原文件 SHA256 记录在
`source_document` 中。

先检查两级质量门：

- `parse_quality.prefill_ready=true`：功能名称可用，可以立即进行S1名称案例预填；
- `parse_quality.analysis_context_ready=true`：章节证据完整，可以进入后续Agent分析；
- `analysis_context_ready=false`：必须先处理 `document_parse_request`，但不能阻断已经满足条件的S1名称预填。

## 总览（新流程）

```
parse DOCX/DOC → intermediate.json
    │
    ├─► Fast Parse：功能名称 → S1名称案例预填
    │
    ├─► 若analysis_context_ready=false 且没有PT exact案例集旁路
    │       document_parse_request.json
    │       → Agent输出agent_document_structure_v1
    │       → document validate / compose
    │       → intermediate_composed.json
    │
    ├─► 若 analysis_context_source=pt_function_case_set_exact
    │       保留未匹配章节作审计记录，不创建Agent文档解析任务
    │
    ├─► 任务B: S1 HARA 判定（仅pending Feature）
    │       → agent_s1.json → merge → s1_decisions.json
    │
    ├─► 任务C: S2 失效模式选择
    │       s2 generate → Agent确认/分析 → s2 compose
    │
    ├─► 任务D: S3 HAZOP
    │       s3 generate → Agent补充语义 → s3 compose
    │
    ├─► 任务E: S4 HARA
    │       hara prepare → Agent审核/填写 → hara validate
    │
    └─► S5整车安全目标 → sg generate
```

严格阶段顺序仍为 `S1 → S2 → S3 → S4 → S5`；不允许S3完成后回头修改S2。

**案例库三级匹配**（PT 域已有 490 条案例）：
- **exact**：完整案例事件（含 description、场景、S/E/C及理由、SG、安全状态、FTTI）已锁定，Agent 不得编辑；全部活跃组 exact 时直接 validate skeleton
- **similar**：相似案例，S/E/C已预填供参考，Agent审核调整
- **template**：提供模板参考，Agent根据当前项目填写
- **none**：无可用案例，Agent按 s4-hara-rules.md 规则推理

***

## 任务A：文档结构与章节证据补充

### 输入

当 `parse_quality.analysis_context_ready=false` 时，读取 parse 同时生成的 `document_parse_request.json`。其中包含有边界的 H1～H8 目录、关键正文切片、功能清单快解析结果和 unresolved 项。`unmatched_chapters` 条目示例如下：

```json
{
  "desc": "[P_func_0001] 档位控制及显示 / 换挡请求及仲裁 (换挡请求及仲裁)",
  "func_id": "P_func_0001",
  "func_name": "档位控制及显示",
  "sub_name": "换挡请求及仲裁",
  "scenario": "换挡请求及仲裁",
  "h2_group": "档位控制及显示",
  "candidate_chapters": [
    {
      "label": "3.1.2 挡位请求及仲裁",
      "h3": "挡位请求及仲裁",
      "desc": "XCU_CCU根据SBW发送的挡位请求信号判定驾驶员挡位请求意图。",
      "h4s": [
        {"label": "3.1.2.1 换挡器的挡位请求", "h4": "换挡器的挡位请求", "desc": "..."}
      ]
    }
  ]
}
```

- `candidate_chapters` 已包含该相关项 H2 组下的**所有章节全文**（含 H3 和 H4 的功能描述），Agent 可直接据此做语义定位，无需再从 `chapters[]` 中检索。

### 数据源

- `unmatched_chapters[].candidate_chapters[]`：候选章节的 label、h3、desc 及 h4s
- `intermediate.json.related_items[].sub_functions[]`：子功能的 name、scenario

### 推理方法

1. 遍历 `unmatched_chapters`，每个条目已包含 `func_id`、`func_name`、`sub_name`、`scenario`
2. 在 `candidate_chapters` 中根据 `sub_name` 和 `scenario` 的语义，选择最合适的 H3 或 H4 节点
3. 优先匹配 H4（更精确），其次 H3
4. 将匹配到的 `chapter_label` 填入 intermediate.json 对应子功能的 `chapter` 字段
5. **兜底**：只使用 `document_context_slice` 和明确的 `evidence_ref`；不得无边界复制全文，也不得在没有证据时声称精确命中

### 匹配原则

- 根据功能语义匹配，不只看字面
- 多个子功能可以匹配同一章节
- 如无法确定最佳匹配，标记为需人工确认
- 优先匹配 H4（更精确），其次 H3

### 输出

禁止直接编辑 `intermediate.json`。输出 `agent_document_structure.json`：

```json
{
  "schema_version": "agent_document_structure_v1",
  "request_id": "docreq_xxx",
  "mode": "supplement_context",
  "document_context": {"schema_version": "document_context_v1", "nodes": []},
  "section_assignments": [
    {
      "func_id": "P_func_0001",
      "feature_list_id": "S-402-04",
      "node_id": "n0047",
      "chapter_label": "3.1.5.2.2.1 EPB驻车及释放控制",
      "description": "原文章节证据摘要",
      "evidence_refs": ["paragraph:359"],
      "reason": "Agent确认该章节覆盖此Feature"
    }
  ],
  "unresolved_items": []
}
```

然后执行：

```powershell
python -X utf8 scripts/run_hara.py document validate intermediate.json agent_document_structure.json --request intermediate_document_parse_request.json
python -X utf8 scripts/run_hara.py document compose intermediate.json agent_document_structure.json --request intermediate_document_parse_request.json -o intermediate_composed.json
```

硬规则：

1. `supplement_context` 不得修改已有 `func_id`、`feature_list_id` 和 `func_name`；
2. `full_structure` 中来源没有ID时可以省略，由compose稳定生成；
3. 每个新增节点和章节关联必须提供 `evidence_refs`；
4. 任意位置出现 `failure_id`、危害ID、安全目标ID、ASIL、`id_range` 都直接失败；
5. 文档Agent只能处理结构证据，不能直接修改S2/S3/S4。

***

## 任务B：S1 HARA 判定

### 输入

`intermediate.json` 中 `s1_rule_is_hara` 为 `null` 的子功能（pending 项）。脚本会在 `agent_work_required` 中显式给出本次任务契约：

- `s1_pending_feature_ids`：Agent 必须逐项提交，集合必须完全一致；
- `s1_locked_feature_ids`：规则已锁定，Agent 不得提交；
- `s1_locked_decisions`：锁定项的脚本判定结果，合并时始终以此为准。

缺少任一 pending、提交规则锁定项、提交不存在的功能/子功能、重复 `func_id` 或重复 `feature_list_id`，都会被 `merge` 阻断，不再采用“Agent 未判定默认是”的兜底。

### 数据源

- `references/s1-rules.md`：排除规则表、LLM判断标准
- **`references/s1-rules.md`** **的「重点复核项（参考=否 但规则未覆盖）」表格**：这 13 项参考 Excel 明确判"否"，规则未覆盖，**必须判为"否"**，否则输出与参考不一致
- `intermediate.json.related_items[]`：相关项及其子功能列表

### 核心原则

> **判断对象是整个相关项，不是单个子功能。** 一个相关项只要核心功能涉及安全，其下所有子功能都纳入 HARA 范围。

### 推理方法

对每个有 pending 子功能的相关项：

1. **判断相关项整体是否需要 HARA**
   - 核心功能是否影响车辆运动控制（驱动/制动/转向/换挡/扭矩）？
   - 核心功能是否涉及高压安全（触电/火灾）？
   - 核心功能是否涉及电池安全（热管理/充放电）？
   - 如核心功能仅涉及：纯信息显示/娱乐、诊断报警机制本身、测试/维护模式、排放法规、辅助动力单元 → 整体不需要 HARA
2. **如整体需要 HARA**，其下所有 pending 子功能 `is_hara = true`
3. **如整体不需要 HARA**，所有 pending 子功能 `is_hara = false`，并给出 `remark`

### 输出格式：s1\_decisions.json

```json
{
  "decisions": [
    {
      "func_id": "P_func_0001",
      "func_name": "档位控制及显示",
      "reason": "档位控制直接影响车辆运动控制，其失效可能导致非预期加减速，需要HARA",
      "sub_functions": [
        {"feature_list_id": "S-402-01", "is_hara": true, "remark": "/"},
        {
          "feature_list_id": "S-409-01",
          "is_hara": false,
          "disposition": "transfer",
          "target_domain": "AD",
          "reason_code": "TRANSFER_TO_ADAS_LONGITUDINAL_CONTROL",
          "remark": "该子功能属于 ADAS 纵向目标/巡航控制，应由 AD 域分析。"
        }
      ]
    }
  ]
}
```

- `func_id`、`func_name`：对应 intermediate.json
- `reason`：Agent 对相关项整体判断的推理理由（50-150字）
- `sub_functions`：**仅列出 pending 的子功能**（规则已判定的不需要）
  - `is_hara`：布尔值
  - `remark`：`"/"` 表示无特殊情况，否则写排除原因
  - `disposition`：可选；仅对 pending 项可写 `analyze`、`transfer` 或 `exclude`。已由 Domain Pack 锁定的子功能不得提交。
  - `target_domain`：仅 `disposition = transfer` 时必填，例如 `AD`、`CB`、`BD`。
  - `reason_code`：声明 `disposition` 时必填；使用简短、稳定的英文大写下划线原因码。

### 域边界与转交原则（必须遵守）

1. **不要把“当前 PT 不分析”一律写成转交。**
   - 明确由其他域负责且仍需由该域开展分析：使用 `transfer`；
   - 纯显示、配置、娱乐等不进入车辆级 HARA：使用 `exclude`；
   - 执行器/控制责任不清：不要静默转交，保持普通 S1 判断并在 `remark` 写明 `needs_review`，由工程复核。
2. `transfer` / `exclude` 必须 `is_hara = false`；`analyze` 必须 `is_hara = true`。
3. 不能只因名称中出现 “ADAS”、“EPB”、“空调” 就转交；必须根据 DOCX 的章节、组件、执行器、控制对象和输入/输出责任判断。
4. Domain Pack 已识别出的 `transfer` / `exclude` 是锁定结论，Agent 不得重复提交或改写。

### Few-shot：跨域与排除示例

**示例 A：明确移交 ADAS**

```json
{
  "feature_list_id": "<pending-feature>",
  "is_hara": false,
  "disposition": "transfer",
  "target_domain": "AD",
  "reason_code": "TRANSFER_TO_ADAS_LONGITUDINAL_CONTROL",
  "remark": "控制对象是巡航目标车速/纵向规划，PT 仅接收结果请求；应由 AD 域分析。"
}
```

**示例 B：纯显示排除，而非假装转交**

```json
{
  "feature_list_id": "<pending-feature>",
  "is_hara": false,
  "disposition": "exclude",
  "reason_code": "DISPLAY_ONLY_NO_CONTROL_OUTPUT",
  "remark": "仅显示电耗或续驶里程，不改变扭矩、高压状态或车辆运动控制，不进入车辆级 HARA。"
}
```

**示例 C：EPB 接口不能机械移交**

```text
“请求 EPB 驻车”不等于自动 transfer 到 CB：
- 若风险来自 PT 的档位/状态决策错误，PT 侧仍可能需要分析；
- 若风险来自 EPB 执行器未按请求动作，应由 CB 侧分析；
- 当前输入无法区分责任时，不写 transfer，标记 needs_review 并说明接口责任待确认。
```

***

## 任务C：S2 失效模式选择

### 输入与证据边界

- `intermediate.json.related_items[].domain_context`：唯一可用的功能语义、分析单元和证据等级上下文；不要只按功能名称、Excel 行号或历史 failure ID 推断。
- 合并后的 `s1_decisions.json`：只有最终 `is_hara=true` 的相关项可以进入 S2。
- `references/s2-failure-modes.md`：11 种失效模式定义与推理要求。

### 受控草稿流程（必须执行）

```powershell
# exact 锁定项与 compatible 待确认项的草稿；不是最终文件。
python -X utf8 scripts/run_hara.py s2 generate intermediate.json s1_decisions.json -o s2_draft.json

# Agent 仅提交 agent_s2.json：compatible 确认/差异和 needs_review 的完整决策。
python -X utf8 scripts/run_hara.py s2 compose intermediate.json s1_decisions.json s2_draft.json agent_s2.json -o s2_decisions.json

python -X utf8 scripts/run_hara.py s2 validate s1_decisions.json s2_decisions.json --intermediate intermediate.json
```

- `exact_known`：草稿已锁定；Agent 不得在 `agent_s2.json` 提交该 `func_id`。
- `compatible`：草稿预填 `analysis_unit_id`、`evidence_status="compatible"`、`compatible_baseline.selected_modes`。Agent 必须提交同一 `func_id` 的确认或差异，不得删除这些来源字段。
- `needs_review`：Agent 提交完整 `func_id`、`func_name`、非空 `selected_modes` 和工程理由。

### Agent 的 `agent_s2.json` 格式

```json
{
  "decisions": [
    {
      "func_id": "P_func_0002",
      "selected_modes": ["丢失", "非预期", "过多", "过少"],
      "reason": "当前项目的高压状态机、执行器对象和诊断边界与 PT compatible 基线一致。",
      "agent_review": {
        "required": true,
        "status": "confirmed",
        "summary": "当前项目与 compatible 基线一致。"
      },
      "compatible_baseline_changes": []
    },
    {
      "func_id": "<needs_review 功能ID>",
      "func_name": "<当前项目功能名>",
      "selected_modes": ["丢失", "非预期"],
      "reason": "<基于当前项目功能边界的 50-150 字理由>"
    }
  ]
}
```

compatible 有差异时，`agent_review.status` 必须为 `adjusted`，每个增减模式都登记 `add_mode` / `remove_mode` 与 `difference_reason`。`confirmed` 时模式集合不得偏离基线。

最终 `s2_decisions.json` 的集合必须与 S1=是的相关项完全一致；`compose` 和 `validate` 会同时阻断漏项、重复、exact 改写以及 compatible 静默降级。

***

## 任务D：S3 HAZOP 分析生成

### 受控草稿与合成

```powershell
# 仅生成 exact 锁定 HAZOP entry；不是最终文件。
python -X utf8 scripts/run_hara.py s3 generate intermediate.json s1_decisions.json s2_decisions.json -o s3_draft.json

# Agent 输出 agent_s3.json，补 compatible 与 needs_review 功能的 entry。
python -X utf8 scripts/run_hara.py s3 compose intermediate.json s1_decisions.json s2_decisions.json s3_draft.json agent_s3.json -o s3_hazop.json

python -X utf8 scripts/run_hara.py s3 validate s1_decisions.json s2_decisions.json s3_hazop.json --intermediate intermediate.json
```

`agent_s3.json` 只包含非 exact entry。对 compatible 范围，compose 会强制写入 `analysis_unit_id`、`evidence_status="compatible"`、`agent_action="review_compatible_scope"` 与 S2 确认记录；Agent 不得把它静默降级为无来源 HAZOP。若 Pack 还存在事件级 compatible 基线，S4 骨架会继续强制 baseline event 的确认/差异登记。

**ID 边界：** `agent_s3.json` 的 `anomalies[]` 严禁出现 `failure_id` 字段。Agent 只提交异常与危害语义；只要提交该字段（包括合法格式、空值、附加后缀或任何变体值），`s3 compose` 就直接失败。compose 在 exact/compatible/Agent 数据合并完成后，根据当前项目功能编号、S2 模式顺序和异常语义稳定排序，统一生成运行期 `failure_id`。Domain Pack 中的 `baseline_failure_id`/`reference_failure_ids` 仍只用于来源追溯，不属于 Agent 的运行期编号字段。


### 输入

S2 判定为需要 HARA 的功能及其选中的失效模式（来自 `s2_decisions.json`）。仅允许 S1 合并结果中 `is_hara = true` 的相关项进入本任务。

若 S1=否的相关项出现在 S2 或 S3，`hara prepare` 不生成骨架，最终 `write` 也拒绝写 Excel；这是硬阻断，不是人工确认警告。

### 数据源

- `intermediate.json.related_items[]`：功能名称、子功能列表、章节全文（含功能描述）
- `s2_decisions.json.decisions[]`：每个功能的 `selected_modes` 和 `reason`
- `references/s3-hazop-quality.md`：HAZOP 质量标准与真实范例

### 核心原则

> **对 S2 选中的每一种失效模式，逐子功能/子场景枚举异常表现。** 一个功能有 N 个选中模式 × M 个子功能场景，就可能产生 N×M 条异常。一个异常可对应多个整车危害（多行），无危害的异常也列出并标注"不涉及"。

### 推理方法

对每个需要 HARA 的功能，遍历其在 S2 中选中的**每一种**失效模式：

1. **理解功能行为**：阅读 `intermediate.json` 中该功能的章节全文（chapters\[].desc、h4s\[].desc），列出所有子功能/子场景
2. **逐模式枚举异常**：对每个选中的失效模式，问"该模式下，哪些子功能会异常？"
   - 状态变化类功能（档位、上下电）：枚举每个状态切换方向（如 P→D、P→R、D→R…）
   - 数值输出类功能（扭矩、制动力、温度）：枚举每个子功能（如加热/冷却、充电/放电）
   - 执行器类功能（EPB、转向）：枚举每个动作方向（如接合/释放）
   - 安全监测类功能（互锁、绝缘）：枚举每个监测项
3. **为每个异常识别整车危害**：
   - 一个异常可能导致多个不同的整车级危害 → 每个危害一行，共用同一个 failure\_id
   - 如果异常不导致整车层面危害（如制动力过大但车辆静止），危害写"无整车层面危害"，关联 HARA 写"不涉及"
   - 危害描述必须是声明式技术后果，不写因果链
4. **异常描述规范**：
   | 规范     | 说明                     | 好                   | 坏                             |
   | ------ | ---------------------- | ------------------- | ----------------------------- |
   | 子功能级别  | 必须具体到子功能/场景，禁止"功能完全丧失" | "P档切入D档位失效"         | "换挡请求及仲裁功能完全丧失"               |
   | 5-15 字 | 简洁精准                   | "非预期接合驻车制动器"        | "EPB系统在驾驶员未请求的情况下错误地接合驻车制动卡钳" |
   | 含模式语义  | 描述中体现失效模式              | "非预期D档切入P"、"驻车制动不足" | "D档切入P异常"（看不出什么模式）            |
5. **危害描述规范**：
   | 规范     | 说明                               | 好             | 坏                                           |
   | ------ | -------------------------------- | ------------- | ------------------------------------------- |
   | ≤ 20 字 | 极度精简（特殊情况可放宽至 25 字）              | "车辆无扭矩输出"（7字） | "车辆无法换挡，停留在当前挡位，若在D挡无法切换至P/R可能导致非预期行驶"（33字） |
   | 声明式    | 只写整车级后果，不写因果链                    | "车辆非预期纵向移动"   | "驾驶员对踏板振动产生误判，松开制动踏板，导致制动距离延长"              |
   | 技术后果   | 使用标准术语：无扭矩/无动力/碰撞/触电/起火/失控/抛锚/甩尾 | "车辆减速度丧失或不足"  | "制动时车辆侧滑，尤其在低附着系数路面导致车辆失稳碰撞"                |
6. **不要分配运行期 ID**：
   - Agent 输出的 `anomalies[]` 省略 `failure_id`
   - `s3 compose` 在合并、分组和稳定排序后生成 `{域}_MF_{功能4位序号}_{2位流水号}`
   - 同一异常的多个危害位于同一个 anomaly 对象中，最终自动共用同一个 `failure_id`
7. **关联 HARA**：S3 阶段统一留空 `""`（"不涉及"的除外）

### 真实范例（来自工程师 HARA 分析）

**EPB（8行，覆盖丢失/非预期/过少/过多）：**

| 模式  | 异常表现        | 功能失效ID           | 整车危害         |
| --- | ----------- | ---------------- | ------------ |
| 丢失  | 接合驻车制动器功能丧失 | CB\_MF\_0002\_01 | 车辆非预期纵向移动    |
| 丢失  | 释放驻车制动器功能丧失 | CB\_MF\_0002\_02 | 车辆丢失纵向运动     |
| 非预期 | 非预期接合驻车制动器  | CB\_MF\_0002\_03 | 非预期车辆横向运动    |
| 非预期 | 非预期接合驻车制动器  | CB\_MF\_0002\_03 | 车辆非预期纵向减速    |
| 非预期 | 非预期释放驻车制动器  | CB\_MF\_0002\_04 | 车辆非预期纵向移动    |
| 非预期 | 动态制动非预期激活   | CB\_MF\_0002\_05 | 车辆非预期纵向减速    |
| 过少  | 驻车制动不足      | CB\_MF\_0002\_06 | 车辆非预期纵向移动    |
| 过多  | 驻车制动力过多     | CB\_MF\_0002\_07 | 无整车层面危害（不涉及） |

**动力域-档位控制（节选，丢失模式枚举所有档位切换）：**

| 模式  | 异常表现      | 整车危害    |
| --- | --------- | ------- |
| 丢失  | P档切入D档位失效 | 车辆无扭矩输出 |
| 丢失  | N档切入D档位失效 | 车辆无扭矩输出 |
| 丢失  | R档切入D档位失效 | 非预期向后移动 |
| 丢失  | P档切入R档位失效 | 车辆无扭矩输出 |
| 丢失  | D档切入R档位失效 | 非预期向前移动 |
| 非预期 | 非预期D档切入P  | 车辆非预期减速 |
| 非预期 | 非预期D档切入N  | 车辆驱动力丢失 |
| 反向  | 非预期D挡进入R挡 | 车辆反向移动  |

### 输出格式：s3\_hazop.json

```json
{
  "domain": "CB",
  "entries": [
    {
      "func_id": "CB_func_0002",
      "func_name": "电子驻车制动",
      "failure_mode": "丢失",
      "anomalies": [
        {
          "description": "接合驻车制动器功能丧失",
          "failure_id": "CB_MF_0002_01",
          "hazards": [
            {"description": "车辆非预期纵向移动", "associated_hara": ""}
          ]
        },
        {
          "description": "释放驻车制动器功能丧失",
          "failure_id": "CB_MF_0002_02",
          "hazards": [
            {"description": "车辆丢失纵向运动", "associated_hara": ""}
          ]
        }
      ]
    },
    {
      "func_id": "CB_func_0002",
      "func_name": "电子驻车制动",
      "failure_mode": "非预期",
      "anomalies": [
        {
          "description": "非预期接合驻车制动器",
          "failure_id": "CB_MF_0002_03",
          "hazards": [
            {"description": "非预期车辆横向运动", "associated_hara": ""},
            {"description": "车辆非预期纵向减速", "associated_hara": ""}
          ]
        }
      ]
    }
  ]
}
```

- `entries[]`：按 `func_id + failure_mode` 分组，每个选中模式一个 entry
- `anomalies[]`：该模式下所有子功能/场景异常，数量取决于功能复杂度
- `anomalies[].hazards[]`：一个异常对应的所有整车危害；无危害时写 `{"description": "无整车层面危害", "associated_hara": "不涉及"}`
- 最终 `s3_hazop.json` 的 `failure_id`：由 compose 生成，同一功能内唯一，同一异常的多个危害共用；`agent_s3.json` 不填写
- `associated_hara`：S3 阶段留空字符串 `""`，"不涉及"的除外

#### PT compatible S3 基线确认/差异合同

当 entry 的 `agent_action="review_compatible_baseline"` 时，候选异常已带 `baseline_failure_id` 和 `reference_failure_ids`。无差异时只确认；不能换运行期 `failure_id`，也不能删改参考来源：

```json
{
  "agent_action": "review_compatible_baseline",
  "evidence_status": "compatible",
  "agent_review": {"status": "confirmed", "summary": "DCDC 控制边界与候选一致。"},
  "compatible_baseline": {
    "failure_mode": "过多",
    "analysis_unit_id": "PT_HV_POWER_ON_OFF",
    "anomalies": [{"baseline_failure_id": "P_MF_0005_03", "reference_failure_ids": ["P_MF_0002_03"]}]
  },
  "compatible_baseline_changes": []
}
```

如需改动：

- 修改基线异常：登记 `{"baseline_failure_id": "...", "action": "modify", "difference_reason": "..."}`；
- 删除基线异常：登记 `remove` 和理由；
- 新增当前项目专属异常：登记 `add` 和理由，并在新增 anomaly 上标 `evidence_status="project_specific"` 和相同的 `difference_reason`。

`confirmed` 不得有实际差异；`adjusted` 必须至少有一项实际差异。未确认的 compatible S2/S3 候选不能进入 S4 骨架。

***

- `references/s4-hara-rules.md`：S/E/C 评定、场景速查表、真实范例
- PT：`references/domain_packs/PT/PT.json` 是唯一场景主数据；场景正文只在 `risk_catalog.scenario_catalog` 中保存。
- CS/CB/AD/ET/BD：`references/scenarios/manifest.json` 与当前域文件（如 `references/scenarios/CS.json`）管理场景规则；不加载其他域。
- 当前功能 Profile（如 `references/profiles/CS_steering_assist.json`）：规定相关项级 HAZOP 粒度和跨域排除
- 当前域语义别名表（如 `references/aliases/CS_feature_aliases.json`）：Feature List ID 只能追溯，不能单独作为功能语义主键

## 任务E：S4 HARA 分析（案例库自动预填）

### 步骤1：生成骨架（脚本，已集成案例库）

```powershell
python scripts/run_hara.py hara prepare s3_hazop.json \
  -o s4_hara_skeleton.json \
  --intermediate intermediate.json \
  --s1 s1_decisions.json \
  --s2 s2_decisions.json
```

`hara prepare` 会：
1. 校验 S1/S2/S3 合同
2. 从 S3 复制所有追溯字段（func_id、failure_id、semantic_key、vehicle_hazard_id、source_case_ids、source_refs）
3. 若受控 S3 已选择案例库 `case_ids`，从同一个 PT 运行时案例资产解引用并展开全部来源事件；只有 S3 没有来源案例时才应用案例库三级语义匹配
4. 生成骨架；全部活跃危害均 locked 时，Agent 不创建或改写事件，直接验证 skeleton

**案例库匹配等级**：
- `exact`：完整案例事件（含 description、场景、S/E/C及理由、SG、安全状态、FTTI）已锁定，`prefill_locked=true`
- `similar`：场景不同或S/E/C有冲突，预填供参考，Agent需审核调整
- `template`：只提供安全目标模板，Agent需完整填写
- `none`：无可用案例，Agent按 s4-hara-rules.md 规则推理

### 步骤2：Agent 填写/审核 events

**重要**：Agent 必须**基于 skeleton 修改**生成 `s4_hara_agent.json`，而不是从头构建新JSON。

**操作步骤**：
1. 读取 `s4_hara_skeleton.json` 完整内容
2. 保留所有追溯字段（`func_id`、`failure_id`、`semantic_key`、`vehicle_hazard_id`、`analysis_unit_id`、`hazard_family`等）
3. 仅对 `agent_action` 允许编辑的危害组修改业务字段；`locked` 组一个字段也不能改：
   - `description`（仅未锁定事件缺失时填写）
   - `scenario`（场景描述，如果skeleton为空或需要调整）
   - `severity`、`severity_reason`（对于similar/template/none）
   - `exposure`、`exposure_reason`（对于similar/template/none）
   - `controllability`、`controllability_reason`（对于similar/template/none）
   - `safety_goal`、`safe_state`、`ftti`（对于template/none）
   - `note`（如果调整了预填值，说明理由）
4. 保存为 `s4_hara_agent.json`

**对于 exact 匹配**（`prefill_locked=true` 或 `agent_action="locked"`）：
- `description`、场景、S/E/C及理由、安全目标、安全状态、FTTI 和来源追溯均已锁定
- Agent **不得修改、删除、重排或补写**这些事件
- 若所有活跃危害组均为 `locked`，直接对 `s4_hara_skeleton.json` 执行 `hara validate`，不要额外生成 Agent 编辑版

**对于 similar 匹配**：
- S/E/C 已预填供参考
- Agent 审核后可以调整
- 调整时在 `note` 字段说明理由

**对于 template/none**：
- Agent 根据 s4-hara-rules.md 完整填写
- 参考 `prefill_guidance` 字段获取评定要点

**禁止事项**：
- ❌ 不要从头构建新JSON，必须基于skeleton修改
- ❌ 不要删除或修改追溯字段（semantic_key、vehicle_hazard_id等）
- ❌ 不要修改 `prefill_locked=true` 事件的锁定字段
- ❌ 不要调用 `hara autofill`（已废弃）
- ❌ 不要填写或沿用 `asil`、`hazard_id`、`safety_goal_id`、`id_range`；这些字段由 `hara validate` 清除后重算/重编

**关键规则**：
- S=0 时，E/C及其理由均为 null，ASIL 留空（不是 QM）
- S>0 且查表为 QM 时，才填 ASIL=QM
- 不同场景的 S/E/C 必须有差异，禁止所有事件相同
- QM 和 S=0 事件必须保留，不能跳过

### 步骤3：验证（脚本）

```powershell
python scripts/run_hara.py hara validate s4_hara.json \
  --s3 s3_hazop.json \
  -o s4_hara_final.json
```

脚本会：
- 计算 ASIL（查 ISO 26262-3 Table 4）
- 分配危害 ID 和安全目标 ID
- 执行 15 条验证规则
- 错误数必须为 0 才能生成 final

**只有 `validation.passed=true` 的 final 才能进入 S5/write。**

---

## 任务F：S5 整车安全目标汇总

### 步骤 1：生成整车安全目标汇总（脚本，必经步骤）

```powershell
python scripts/run_hara.py sg generate s4_hara_final.json -o safety_goals.json
```

脚本自动完成：

- 从 `s4_hara_final.json` 提取所有有安全目标文本的非 QM 事件（跳过 skip）
- 按安全目标文本**精确匹配**去重，同一 SG 跨功能/跨异常合并
- ASIL 取所有关联事件的最高等级（D > C > B > A）
- 功能/异常/安全状态/FTTI 去重聚合
- 分配整车级编号 `{域}_SG_VH_{4位流水}`（如 `CB_SG_VH_0001`），按 ASIL 降序、同 ASIL 按文本字典序排列
- 输出 `safety_goals.json`
- S5 会先核验 S4 的 `validation.passed=true`、`error_count=0` 与无 `.validation_failed` 标记；缺少、失败或存在失败标记时会硬阻断，不会生成安全目标文件

### 步骤 2：写入 Excel（脚本）

在最终 `write` 命令中传入 `--s5 safety_goals.json`，脚本将在"整车安全目标"Sheet 写入：

- 左区 A-E：事件级明细（每个非 QM 有 SG 的危害事件一行）
- 右区 G-L：整车级汇总（每个唯一 SG 在组首行填写）

### 注意事项

- **此步骤不可跳过**，即使脚本输出"去重后安全目标: 0 个"（全 QM 域如 ET 座舱域的正常情况），仍须将生成的 `safety_goals.json` 传入 `write --s5`
- S5 是纯脚本步骤，Agent 不需要推理，只需执行命令并检查输出是否合理
- 规则详见 `references/s5-safety-goal-rules.md`

***

## 合并与验证

Agent 完成推理后，执行以下命令合并结果：

```powershell
# 严格验证 S1：同时校验 JSON 格式、pending 集合和规则锁定范围
python -X utf8 scripts/run_hara.py merge intermediate.json s1_decisions.json --validate-only

# 合并 S1 决策；校验失败时返回非零退出码，且不会生成新的合并结果
python -X utf8 scripts/run_hara.py merge intermediate.json s1_decisions.json -o s1_decisions.json
```

***

## 最终写入

**最终产物必须包含 HAZOP 分析表、HARA 分析表和整车安全目标表，务必使用 --s3、--s4 和 --s5 参数。**

```powershell
# 写入最终 Excel（含5个数据sheet）
python scripts/run_hara.py write intermediate.json s1_decisions.json s2_decisions.json --s3 s3_hazop.json --s4 s4_hara_final.json --s5 safety_goals.json -o output/result.xlsx
```

> 注意：`sg generate` 不可跳过。即使该域所有事件均为 QM（如 ET 座舱域），也必须执行此命令（输出 0 条安全目标），并将生成的 `safety_goals.json` 传入 `write --s5`。

> 调试用（不写 HAZOP/HARA/整车安全目标，非最终产物）：
>
> ```powershell
> python scripts/run_hara.py write intermediate.json s1_decisions.json s2_decisions.json -o output/result_debug.xlsx
> ```

---

## 附录：PT S4 受控发布合同（现行）

### 1. 项目 ID 与案例语义的边界

- `func_id`、`failure_id`、功能排序和 Excel 行号只用于当前项目追溯，不能作为跨项目案例匹配键；
- Domain Pack/案例资产中的来源项目 `failure_id` 不得复制到当前项目；
- 当前项目失效 ID 由运行期统一分配，例如 `P_func_0005 -> P_MF_0005_xx`、`F16 -> P_MF_0016_xx`；
- `semantic_key` 由当前项目 `analysis_unit_id + failure_mode + anomaly + vehicle_hazard` 生成，用于发现“ID 未变但语义被替换”。

### 2. 候选证据等级

- `exact`：完整语义键和 assessment 一致时才能锁定；
- `similar`：功能/失效/危害语义相近但场景不完全一致，只能作为可编辑候选；
- `template`：仅功能族或模式相近，不能直接当作当前项目场景/S/E/C；
- `none`：由当前项目工程分析新建事件。

当 template 候选与当前项目异常或危害不一致时，应优先使用当前项目相关项定义、当前项目 HARA 基线或正式工程记录进行修正，不能机械复制第一条候选。

### 3. 危害映射审批记录

PT 的 `vehicle_hazard_id` / `hazard_family` 是受控域资产。候选映射进入正式输出前必须成为 `exact_locked`、`compatible_verified` 或有真实审核记录的 `engineering_approved`。审批记录格式：

```json
{
  "status": "engineering_approved",
  "source": "engineering_review",
  "approval": {
    "reviewer": "功能安全工程师姓名或工号",
    "approved_at": "YYYY-MM-DD",
    "reference": "审核记录/变更单编号"
  }
}
```

端到端测试可以复用用户提供的已批准项目 HARA 作为验收证据，但必须明确标注为测试基线复用，不能声称本次运行产生了新的人员审批。

### 4. PT S4 正式命令链

```powershell
# 1) 生成受控骨架
python -X utf8 scripts/run_hara.py hara prepare s3_hazop.json --intermediate intermediate.json --s1 s1_decisions.json --s2 s2_decisions.json -o s4_hara_skeleton.json

# 2) Agent/工程师只填写允许的工程字段，保存为 s4_hara_agent.json

# 3) 只补确定性追溯字段；不会替 Agent 作工程判断
python -X utf8 scripts/run_hara.py hara autofill s4_hara_agent.json -o s4_hara_ready.json

# 4) PT 必须传入同一轮 S3，验证语义主键与危害映射
python -X utf8 scripts/run_hara.py hara validate s4_hara_ready.json --s3 s3_hazop.json -o s4_hara_final.json

# 5) S5 质量闸门与 Excel 发布
python -X utf8 scripts/run_hara.py sg generate s4_hara_final.json -o safety_goals.json
python -X utf8 scripts/run_hara.py write intermediate.json s1_decisions.json s2_decisions.json --s3 s3_hazop.json --s4 s4_hara_final.json --s5 safety_goals.json -o output/result.xlsx
```

只有 `hara validate` 成功生成且 `validation.passed=true` 的 S4 可以进入 S5。S5 error 会在写文件前阻断，必须回到 `s4_hara_agent.json` 修正并重跑，不能直接修改 final/S5，也不能用 `--force` 绕过。

### 5. `hara autofill` 的严格边界

`hara autofill` 只继承或补齐 `analysis_unit_id`、`failure_id`、`vehicle_hazard_id`、`hazard_family`、`semantic_key`、映射状态/来源及 `needs_review` 的证据状态。

它不会创建 event、选择或扩张场景、填写/改变 S/E/C、计算或覆盖 ASIL、猜测 `vehicle_hazard_id`、升级候选映射，或填写安全目标/安全状态/FTTI。

### 6. needs_review 受控任务表单

PT 的 `create_needs_review_events` 危害组由 `hara prepare` 附带不可改写的 `agent_task`：

- `required_event_fields`：场景、危害事件描述及 S/E/C 与理由；
- `conditional_rules`：S=0 时 E/C、理由、ASIL、SG/Safe State/FTTI 留空；ASIL=A/B/C/D 时必须填写事件级 SG/Safe State/FTTI；
- `forbidden_actions`：不得猜危害 ID、改 `failure_id`/`semantic_key`、改 exact/compatible 基线、将其他域或旧项目当成当前事实，或写运行时修复脚本。

`hara validate` 会验证任务合同未被篡改。若当前需求超出该表单（例如新增危害族），应更新受控域资产并进行工程审核，而不是删除校验字段。
