---
name: hara-generator
description: 根据汽车相关项定义文档（.docx / 旧 .doc）自动生成HARA分析Excel的Skill。支持文档解析、S1规则预判、章节匹配、S1危害判定、S2失效模式选择、S3 HAZOP分析生成、S4 HARA分析（S/E/C评定+ASIL计算+安全目标）、S5整车安全目标汇总去重编号和Excel导出。零外部LLM依赖。
---

# HARA 危害分析与风险评估 Skill

## 简介

将汽车相关项定义文档（.docx / 旧 .doc）自动转化为 HARA 分析 Excel，覆盖：

- 功能清单解析 + S1 规则预判 + 章节精确匹配
- S1：Agent 自行推理判定 HARA（相关项级）
- S2：Agent 自行推理选择失效模式（11 种）
- S3：Agent 自行推理生成 HAZOP 分析（功能异常表现 + 整车危害）
- S4：Agent 评定 S/E/C，脚本计算 ASIL/分配 ID/验证，生成 HARA 分析表
- 结果写入标准模板 Excel

## 场景速查表（按域拆分）

运行时场景资产按域单源读取：

- **PT**：唯一主数据为 `references/domain_packs/PT.json`。场景正文仅保存在 `risk_catalog.scenario_catalog`，其他 PT 风险结构只能保存 `scenario_id`；`references/scenarios/PT.json` 和旧总表中的 PT 分支均不得存在。
- **PT S1 权威资产（部署必需）**：`references/domain_packs/PT_subfunctions.json` 必须与 `PT.json` 一起部署。它是由离线 `PT_子功能.XLSX` 提取的 PT 子功能级 HARA 判定资产；运行时只读取 JSON，不读取或依赖原始 XLSX。缺失时脚本必须显式报告资产缺失，不得把全部子功能静默交给 Agent。
- **PT 功能级 exact 路由**：当 DOCX 中的 HARA 正向整车功能能由 PT 案例集唯一锁定时，S2～S5 使用该功能级案例集；子功能语义覆盖差异只作为 `raw_*` 诊断保留，不得向 Agent 暴露为可操作的 `unknown`/`needs_review`。章节未匹配仍可保留审计记录，但不阻断该 exact HARA 路径。
- **CS / CB / AD / ET / BD**：场景文件位于 `references/scenarios/`，由 `references/scenarios/manifest.json` 管理。
- `references/profiles/CS_steering_assist.json`：转向相关项级 HAZOP 粒度约束。
- `references/aliases/CS_feature_aliases.json`：转向功能语义别名及 Feature List ID 版本漂移记录。

脚本按域懒加载当前查询所需资产。PT 已禁止回退到旧总表或全局 SEC 参考库；Agent 只读取当前域与当前功能 Profile，不加载全量场景表。

> **开发/资产发布前校验（非 Aily 生产工作流）**：`scripts/tools/validate_scenario_assets.py` 仅用于本地维护时校验拆分后的场景资产与旧总表的迁移一致性。它不参与 `DOCX → S1 → S2 → S3 → S4 → S5 → Excel` 正式链路；Aily 生产部署包无需包含 `scripts/tools/`，Agent 也不得在单次 HARA 任务中调用该脚本。

**最终产物必须包含 5 个数据 Sheet**：相关项功能清单 + 失效模式选择 + HAZOP 分析 + HARA 分析 + 整车安全目标。其中整车安全目标通过 `sg generate` + `write --s5` 生成（ET 等全 QM 域可能为 0 条，但命令仍须执行）。模板中其余 Sheet（封面、版本管理、命名规则、参考场景、评定参考、ASIL 判定等）保持原样不动，只在上述数据 Sheet 中填写数据。

**本 Skill 不调用任何外部 LLM API**，所有推理由调用方 Agent 完成。

## 触发条件

当用户提供汽车相关项定义 .docx 或旧 .doc 文件，要求进行 HARA 分析、危害分析、风险评估时使用本 Skill。

## 前置条件

- Python 3.12+；命令统一使用已验证解释器 `python -X utf8`（可在虚拟环境中执行）
- 依赖：`python-docx`、`openpyxl`（无 LLM 相关依赖）

## 执行流程（六阶段）

### 阶段一：解析文档（脚本）

```powershell
python -X utf8 scripts/run_hara.py parse "<输入.docx或.doc>" -o intermediate.json
```

输出 `intermediate.json`，包含：

- 所有相关项及子功能
- 第3章完整结构
- S1 规则预判结果（关键词匹配已完成）
- 待 Agent 判定的 pending 子功能列表
- 未匹配/低置信章节列表及有边界的 `document_context_v1`
- `parse_quality.prefill_ready` 与 `analysis_context_ready` 两级质量门
- 原文件 SHA256、格式识别和旧 DOC 转换追溯

S1 名称预填只依赖 `prefill_ready=true`，不等待全部子功能章节匹配。若
`analysis_context_ready=false`，parse 会同时生成
`*_document_parse_request.json`。Agent 必须输出 `agent_document_structure_v1`，再执行：

```powershell
python -X utf8 scripts/run_hara.py document validate intermediate.json agent_document_structure.json --request intermediate_document_parse_request.json
python -X utf8 scripts/run_hara.py document compose intermediate.json agent_document_structure.json --request intermediate_document_parse_request.json -o intermediate_composed.json
```

Agent 不得直接编辑 intermediate，也不得在文档结构结果中提交 `failure_id`、危害ID、
安全目标ID、ASIL或其他S2/S3/S4运行期字段。后续统一使用compose后的intermediate。

### 阶段二：Agent 推理 S1 + S2

> **S1 事实源边界（硬规则）**：Agent 只能输出 pending 子功能的原始 S1 决策；必须执行 `merge` 生成最终 `s1_decisions.json` 后，才可进入 S2/S3/S4/write。最终 S1 的相关项级 `is_hara` 必须等于子功能 `is_hara` 的聚合值，且所有 Domain Pack 已锁定的 Feature 不得改写；任一不一致都会阻断下游。

**任务 A：章节匹配与 S1 判定**（详见 `${SKILL_DIR}/references/agent-workflow.md`）

- 如文档质量门未通过，先按 document_parse_request 补充章节证据并执行 `document compose`；禁止直接编辑 intermediate。
- 只提交 `agent_work_required.s1_pending_feature_ids`；该列表使用 `func_id/feature_list_id` 组合键，Agent JSON 则在外层使用 `func_id`、内层使用裸 `feature_list_id`。
- `s1_locked_feature_ids` 是规则/Pack 锁定项，Agent 不得提交或覆盖。

```powershell
python -X utf8 scripts/run_hara.py merge intermediate.json agent_s1.json -o s1_decisions.json
```

**任务 B：S2 受控草稿、确认与合成**

先读 `intermediate.json.related_items[].domain_context`，不可只根据功能名称或顶层状态判断：

- `exact_known` 且最终 S1=是子功能被同一精确 `analysis_unit_id` 完整覆盖：由脚本锁定，Agent 不得提交或改写。
- 所有最终 S1=是子功能均被同一 `compatible` 分析单元覆盖：脚本生成 compatible 候选；Agent 必须确认基线，或逐项登记模式差异，不能降级成普通决策。
- 其余 `needs_review` / 未覆盖功能：Agent 按当前项目推理完整 S2。

```powershell
# 仅生成受控草稿：exact 锁定项 + compatible 待确认项；不是最终 S2。
python -X utf8 scripts/run_hara.py s2 generate intermediate.json s1_decisions.json -o s2_draft.json

# Agent 输出 agent_s2.json：只提交 compatible 确认项和 needs_review 项；不得提交 exact 锁定项。
python -X utf8 scripts/run_hara.py s2 compose intermediate.json s1_decisions.json s2_draft.json agent_s2.json -o s2_decisions.json

# 最终 S2 强校验；compatible 项缺少确认、来源快照或差异登记都会阻断。
python -X utf8 scripts/run_hara.py s2 validate s1_decisions.json s2_decisions.json --intermediate intermediate.json
```

对 compatible 项，Agent 只能：① `agent_review.status="confirmed"` 并说明当前项目一致；或② `"adjusted"`，对每个增/减模式登记 `add_mode` / `remove_mode` 与 `difference_reason`。不得删减预填模式、抹掉 `evidence_status="compatible"` 或删除基线快照。

### 阶段三：Agent 推理 S3 HAZOP 分析

**任务D：S3 HAZOP 分析生成**（详见 `${SKILL_DIR}/references/agent-workflow.md` 和 `${SKILL_DIR}/references/s3-hazop-quality.md`）

- 读 `intermediate.json` + `s2_decisions.json`
- **覆盖 S2 选中的所有模式**（丢失/非预期/过多/过少/反向/卡滞等），不是只做"丢失"
- 每个模式逐子功能/子场景枚举异常（状态切换类枚举每个方向，数值类枚举每个子功能）
- 一个异常可对应多个危害（多行共用 failure\_id）；无危害的异常也列出，标注"不涉及"
- 异常描述 5-15 字到子功能级别，禁止"功能完全丧失"
- 危害描述 ≤ 20 字声明式技术后果，禁止因果叙事链
- Agent 输出 `agent_s3.json`；先由脚本生成 `s3_draft.json`，再通过 `s3 compose` 合成为最终 `s3_hazop.json`（格式见 agent-workflow\.md）
- **PT compatible（不是锁定结论）**：S3 必须保留 compatible 范围追溯；无可靠事件级基线时，仍须保留 `analysis_unit_id`、`evidence_status="compatible"`、`agent_action="review_compatible_scope"` 和来自 S2 的确认记录。存在事件级 compatible 基线时，还必须保留 `baseline_failure_id` 和 `reference_failure_ids`，任何 modify/remove/add 都需登记差异理由。

> **S3 是强制步骤，不可跳过。** 最终产物必须包含 HAZOP 分析表。

### 阶段四：S4 HARA 分析（案例库自动预填）

**步骤1：生成骨架（脚本，已集成案例库）**

```powershell
python -X utf8 scripts/run_hara.py hara prepare s3_hazop.json -o s4_hara_skeleton.json --intermediate intermediate.json --s1 s1_decisions.json --s2 s2_decisions.json
```

脚本按以下优先级自动预填：
- **Domain Pack exact**：精确矩阵锁定，S/E/C/场景/描述/SG/安全状态/FTTI 完全锁定
- **S3 来源案例 exact**：当受控 S3 已携带 `source="case_library"` 和 `case_ids` 时，S4 必须解引用同一运行时案例资产并展开全部来源场景；不得丢弃来源后重新做无场景语义搜索
- **Domain Pack compatible**：PT 域参考基线，需 Agent 确认
- **案例库 v2 重新匹配**：仅在 S3 没有受控来源案例时执行 semantic exact/similar/template
- **空骨架**：无匹配案例，Agent 完整推理

**案例库三级匹配**：
- **exact**：完整案例事件已锁定（包括 `description`、场景、S/E/C及理由、SG、安全状态、FTTI），`prefill_locked=true`；Agent 不得编辑
- **similar**：相似案例，S/E/C已预填供参考，Agent审核后可调整
- **template**：功能族模板，Agent根据当前项目填写
- **none**：无可用案例，Agent按 `references/s4-hara-rules.md` 推理

案例库使用语义匹配，不依赖项目 `func_id`。PT 的 490 条案例内嵌在 `references/domain_packs/PT.json.case_library_catalog.cases`；其他域如未来启用独立 v2 案例资产，仍只能作为各自域的扩展点。

**步骤2：Agent 填写/审核 events**

**操作方式**：必须**基于skeleton修改**生成 `s4_hara_agent.json`，保留所有追溯字段。

1. 读取 `s4_hara_skeleton.json` 完整内容
2. 保留所有结构和追溯字段（`func_id`、`failure_id`、`semantic_key`、`vehicle_hazard_id` 等）
3. 仅对未锁定危害组修改允许的业务字段；先看 `agent_action`，不要仅凭字段是否为空自行决定
4. 根据 `agent_action` / `prefill_source` 处理：
   - `agent_action="locked"` 或 `prefill_locked=true`：不修改任何 event 业务字段；若全部活跃组均 locked，直接用 skeleton 执行 `hara validate`
   - `similar`：审核候选的场景和 S/E/C，可调整并在 `note` 说明理由
   - `template/none`：按 `s4-hara-rules.md` 完整填写
5. 保存为 `s4_hara_agent.json`

**禁止**：从头构建新JSON、删除追溯字段、调用 `hara autofill`（已废弃）

**步骤3：验证（脚本）**

```powershell
python -X utf8 scripts/run_hara.py hara validate s4_hara_agent.json --s3 s3_hazop.json -o s4_hara_final.json
```

只有错误数为0且 `validation.passed=true` 才是正式 `s4_hara_final.json`。

### 阶段六：S4 受控字段与验证规则

执行 `hara prepare` 必须同时传入 `--intermediate`、`--s1` 和 `--s2`。脚本会复核 Domain Pack 锁定的最终 S1 合同，并硬阻断任何 S1=否相关项进入 S2 或 S3；即使使用 `write --force`，该边界也不能绕过。

脚本从 s3_hazop.json 提取所有危害组，自动：
- 识别"不涉及"并标记 skip
- 推断功能类型（转向/EPS/IBS/EPB/车辆保持/报警/ESC）
- 按危害方向预填安全状态和 FTTI
- **若命中 Domain Pack 精确矩阵**：按 `analysis_unit_id + failure_id + scenario_id` 锁定 S/E/C、理由、危害描述、安全目标、安全状态和 FTTI；不加载旧参考数据库，不允许 Agent 改写
- **PT Domain Pack 路径**：若 S3 已锁定 `source_case_ids`，S4 先完整解引用全部来源事件并设置 `source_case_locked=true`、`prefill_source="case_library_exact"`、`agent_action="locked"`；`hara validate` 会重新解引用案例资产并阻断任何改写。其余危害组再按 `agent_action` 分流。`locked` 禁止编辑；`review_compatible_baseline` 必须确认 PT 基线或登记每一项项目差异；`create_needs_review_events` 可收到案例库 v2 的 `similar/template` 候选，但仍须结合当前 PT 输入确认、修改或新建。PT Pack 未覆盖时禁止回退旧场景总表、旧案例库、全局 SEC 或 CS 内容。
- **确定性字段边界**：Agent 不得填写或保留最终 `asil`、`hazard_id`、`safety_goal_id`、`id_range`；`hara validate` 会先清除这些派生字段，再按当前项目 S/E/C 和项目功能编号重新生成。来源 ASIL/ID 只保存在追溯字段中。
- 非精确矩阵域：优先尝试案例库 v2 语义候选；没有 v2 资产/候选时，才进入该域现有场景/参考规则或 Agent 评定。PT 只允许 Domain Pack + 案例库 v2，不回退旧总表。
- 每个 event 标注 `sec_source`：`domain_pack_exact` / `domain_pack_compatible` / `case_library_exact` / `case_library_similar` / `case_library_template` / `reference_exact` / `reference_pattern` / `agent_evaluated`
- S2→S3 失效模式覆盖检查（传入 `--s2` 时，缺失模式报 warning）
- failure_id 格式校验、异常/危害描述字数校验

**步骤 2：Agent 确认 events**

- 读 `references/s4-hara-rules.md`（核心规则：标准场景集、S/E/C 评定、安全目标模板）
- **先看 `agent_action`**：
  1. `locked`：不编辑；
  2. `review_compatible_baseline`：检查 PT compatible 基线。无差异时填 `agent_review.status="confirmed"`；有差异时填 `"adjusted"` 并逐条写 `compatible_baseline_changes` 的理由；保留的基线 event 必须保留 `baseline_event_id`，并保持 `evidence_status="compatible"`；
  3. `create_needs_review_events`：依据当前 PT 项目输入审查 `prefill_candidates`；可采纳、修改或新建 event，但必须写 `evidence_status="needs_review"`、选择/差异理由和组级 review summary；
  4. 其他旧域路径才按 `reference_exact` / `reference_pattern` / `agent_evaluated` 处理。
- **禁止事项**：对 `locked` 修改 scenario/safe_state/ftti；对 compatible 基线静默改动/删除；PT Pack 未覆盖时套用旧场景表或 CS 内容；使用 `--force` 跳过验证。
- ASIL 由脚本计算，Agent 不填；ID 由脚本分配
- 输出 `s4_hara.json`（在骨架上确认/微调 events）

**步骤 3：验证 + 计算 ASIL/ID（脚本）**

```powershell
python scripts/run_hara.py hara validate s4_hara.json -o s4_hara_final.json --s3 s3_hazop.json
```

脚本自动：
- 查 ISO 26262-3 Table 4 计算 ASIL（S=0 时 ASIL 留空；S>0 且 C=0 或矩阵判定为 QM 时为 QM；S=0 的 E/C 必须留空且不生成安全目标；含工程判断例外覆盖）
- 分配危害事件 ID（`{域}_hzrd_{功能2位}{3位流水}`）和安全目标 ID（每个非 QM 事件独立编号）
- 验证 15 条规则（必填字段、S/E/C 范围、SG 完整性等）
- 输出 ASIL 分布统计和错误/警告
- **仅当错误数为 0 时**才原子写出正式 `s4_hara_final.json`，并写入 `validation.passed=true`、错误数和警告数；有错误时返回非零，既不生成也不覆盖正式 final 文件
- 若目标 final 路径上已有旧文件，新一轮 S4 验证失败会写入同路径的 `.validation_failed` 标记；S5 和 write 会拒绝使用旧 final，直至重新验证成功自动清除标记

> `s4_hara_final.json` 是阶段状态契约，不是普通中间文件。历史 final 文件若缺少 `validation` 字段，必须重新执行新版 `hara validate` 后才能进入 S5 或最终写入。

### 阶段五：S5 整车安全目标汇总（脚本）

```powershell
python scripts/run_hara.py sg generate s4_hara_final.json -o safety_goals.json
```

脚本自动从 `s4_hara_final.json` 提取所有有安全目标的非 QM 事件，按受控工程语义（域、危害族、规范安全目标、安全状态、FTTI）去重，**不以 ASIL 分组**；同一整车级安全目标的 ASIL 取关联事件最高值，再分配整车级编号（`{域}_SG_VH_{4位流水}`，如 `P_SG_VH_0001`），输出 `safety_goals.json`。

S5 会在写文件前执行质量校验。Agent 新建/可编辑事件的 SG 描述过短、必填工程语义缺失等 `error` 会使 `sg generate` 返回非零且不覆盖已有有效文件；应回到 `s4_hara_agent.json` 修正后重新执行 `hara validate -> sg generate`。`risk_matrix_locked=true` 或 `source_case_locked=true` 的权威 exact SG 不适用自由文本长度启发式，Agent 不得为通过 S5 而改写。`write --s5` 也会拒绝包含 error 的旧/stale S5 文件，`--force` 不能绕过。

### 阶段六：写入最终 Excel

```powershell
python scripts/run_hara.py write intermediate.json s1_decisions.json s2_decisions.json --s3 s3_hazop.json --s4 s4_hara_final.json --s5 safety_goals.json -o output/result.xlsx
```

输出 Excel 含 5 个数据 Sheet（相关项功能清单 + 失效模式选择 + HAZOP 分析 + HARA 分析 + 整车安全目标），模板其余 Sheet 完整保留。

## 完整命令链（新流程）

```powershell
# Stage 1: 解析文档
python -X utf8 scripts/run_hara.py parse <输入.docx> -o intermediate.json

# Stage 2: Agent 推理 S1 判定
# 读取 intermediate.json，Agent 推理后输出 agent_s1.json

# Stage 3: 合并 S1
python -X utf8 scripts/run_hara.py merge intermediate.json agent_s1.json -o s1_decisions.json

# Stage 4: S2 generate + Agent 推理 + compose
python -X utf8 scripts/run_hara.py s2 generate intermediate.json s1_decisions.json -o s2_draft.json
# Agent 处理后输出 agent_s2.json
python -X utf8 scripts/run_hara.py s2 compose intermediate.json s1_decisions.json s2_draft.json agent_s2.json -o s2_decisions.json
python -X utf8 scripts/run_hara.py s2 validate s1_decisions.json s2_decisions.json --intermediate intermediate.json

# Stage 5: S3 generate + Agent 推理 + compose
python -X utf8 scripts/run_hara.py s3 generate intermediate.json s1_decisions.json s2_decisions.json -o s3_draft.json
# Agent 处理后输出 agent_s3.json
python -X utf8 scripts/run_hara.py s3 compose intermediate.json s1_decisions.json s2_decisions.json s3_draft.json agent_s3.json -o s3_hazop.json
python -X utf8 scripts/run_hara.py s3 validate s1_decisions.json s2_decisions.json s3_hazop.json --intermediate intermediate.json

# Stage 6: S4 受控草稿 + 工程确认 + 正式验证
python -X utf8 scripts/run_hara.py hara prepare s3_hazop.json --intermediate intermediate.json --s1 s1_decisions.json --s2 s2_decisions.json -o s4_hara_skeleton.json
# Agent/工程确认后输出 s4_hara_agent.json
python -X utf8 scripts/run_hara.py hara autofill s4_hara_agent.json -o s4_hara_ready.json
python -X utf8 scripts/run_hara.py hara validate s4_hara_ready.json --s3 s3_hazop.json -o s4_hara_final.json

# Stage 7: S5 安全目标汇总
python -X utf8 scripts/run_hara.py sg generate s4_hara_final.json -o safety_goals.json

# Stage 8: 写入 Excel
python -X utf8 scripts/run_hara.py write intermediate.json s1_decisions.json s2_decisions.json --s3 s3_hazop.json --s4 s4_hara_final.json --s5 safety_goals.json -o output/result.xlsx
```

`write` 成功后会生成同名 `<result.xlsx>.run_manifest.json`。该文件记录运行时源码、域资产和 S1～S5/Excel 工件的 SHA-256，是正式交付的一部分。

## 参考文档

| 文件                               | 用途                                        |
| -------------------------------- | ----------------------------------------- |
| `references/agent-workflow.md`   | Agent 执行完整工作流指引（必读，含任务A/B/C/D/E/F）           |
| `references/s1-rules.md`         | S1 排除规则表、域代码、判断标准                         |
| `references/s2-failure-modes.md` | 11 种失效模式定义、10 个 few-shot 案例、历史参考案例库说明        |
| `references/s2-lookup-table.json` | S2 历史参考案例库（104 条，来自 11 个数据组，parse 阶段自动匹配 Top-3 ||
| `references/s3-hazop-quality.md` | S3 HAZOP 质量标准：枚举策略、异常/危害规范、好/坏对比、四数据组真实范例 |
| `references/s4-hara-rules.md`    | S4 HARA 核心规则：标准场景集、S/E/C 评定、ASIL 表、安全目标模板、真实范例 |
| `references/sec_reference_database.json` | S/E/C 参考数据库（1402条记录，388唯一场景，从11个数据组参考Excel提取，hara prepare 自动匹配预填） |
| `references/s5-safety-goal-rules.md` | S5 整车安全目标规则：提取/去重/编号规则、Sheet 双区列定义、与 S4 事件级 ID 的关系 |
| `references/reference-scenarios.md` | 144 条参考场景库（按道路/速度/交通参与者分类，含 E 等级）     |
| `references/excel-format.md`     | Excel 输出格式规范（5 Sheet 列结构）                 |

## 脚本清单

### 唯一入口

| 脚本                    | 子命令           | 用途                                        |
| --------------------- | ------------- | ----------------------------------------- |
| `scripts/run_hara.py` | `parse`       | 阶段一：解析 DOCX/DOC → intermediate.json        |
| <br />                | `merge`       | 合并规则预判 + Agent S1 决策 → s1\_decisions.json |
| <br />                | `s2 generate / compose / validate` | 生成受控草稿、合并 Agent 复核并校验最终 S2 |
| <br />                | `s2 validate` | 严格校验 S2 功能范围、完整性、模式和 Pack 锁定项 |
| <br />                | `s3 generate / compose / validate` | 生成受控草稿、合并 Agent HAZOP 并校验最终 S3 |
| <br />                | `s3 validate` | 严格校验 S3 范围、结构和 Pack 锁定项 |
| <br />                | `hara finalize` | 兼容便捷命令：生成案例库语义预填草稿；`validation.passed=false`，不能进入 S5/write |
| <br />                | `hara prepare` | S4 正式入口：校验 S1/S2/S3/Domain Pack 合同并生成受控骨架 |
| <br />                | `hara validate`| S4 正式闸门：计算 ASIL/分配 ID/完整验证，仅成功时输出 final |
| <br />                | `sg generate`  | S5: 从 s4\_final 提取去重编号安全目标 → safety\_goals.json |
| <br />                | `write`       | 阶段六：JSON 决策 → Excel（需 --s3 --s4 --s5）|

### utils/ — 纯工具库（被 run\_hara.py 调用，无 CLI）

| 文件                         | 职责                                                              |
| -------------------------- | --------------------------------------------------------------- |
| `utils/data_models.py`     | 数据类（SubFunction、RelatedItem、HaraEvent、HaraHazardGroup） + 常量 |
| `utils/docx_parser.py`     | 功能清单快路径 + H1～H8 最小上下文 + 兼容章节结构                                           |
| `utils/chapter_matcher.py` | 章节精确/模糊匹配（三级降级）                                                 |
| `utils/s1_rules.py`        | S1 关键词规则引擎 + intermediate.json 构建                               |
| `utils/hara_rules.py`      | S1 下游边界/S2/S3 输出校验 + S4 规则引擎：ASIL 查表、功能类型配置、ID 分配、验证规则 |
| `utils/excel_writer.py`    | Excel 写入引擎（ExcelWriter 类，含 HAZOP/HARA 写入）              |

### stages/ — 管道阶段编排（被 run\_hara.py 调度）

| 文件                      | 对应子命令           | 职责                              |
| ----------------------- | --------------- | ------------------------------- |
| `stages/stage_parse.py` | `parse`         | 编排解析→匹配→规则→输出 intermediate.json |
| `stages/stage_merge.py` | `merge`         | 合并规则结果 + Agent S1 决策（含严格范围验证） |
| `stages/stage_s2.py` | `s2 generate/compose/validate` | 生成草稿、合成最终 S2，并锁定 exact/compatible 合同 |
| `stages/stage_s3.py` | `s3 generate/compose/validate` | 生成草稿、合成最终 S3，并保留 compatible 追溯 |
| `stages/stage_hara.py`  | `hara prepare/validate` | S4 编排：骨架生成、ASIL 计算、ID 分配、验证   |
| `stages/stage_safety_goal.py` | `sg generate` | S5 编排：安全目标提取、去重、编号          |
| `utils/safety_goal_rules.py` | —              | S5 纯函数：提取、去重、ASIL聚合、编号          |
| `stages/stage_write.py` | `write`         | 编排读取 JSON→ExcelWriter→输出 xlsx（5 数据 Sheet，含整车安全目标）   |

## 数据流（新流程）

```
输入 .docx
    │
    ▼
[run_hara.py parse]
    │ 调用 utils/docx_parser + chapter_matcher + s1_rules
    ▼
intermediate.json
    │
    ├──► [Agent 任务A] 章节匹配 → 填充 intermediate.json
    ├──► [Agent 任务B] S1 判定 → agent_s1.json
    │
    ▼
[run_hara.py merge] ──► s1_decisions.json（合并后）
    │
    ▼
[run_hara.py s2 generate] ──► s2_draft.json
    │
    ▼
[Agent 任务C] S2 失效模式选择 → agent_s2.json
    │
    ▼
[run_hara.py s2 compose] ──► s2_decisions.json
    │
    ▼
[run_hara.py s3 generate] ──► s3_draft.json
    │
    ▼
[Agent 任务D] S3 HAZOP 分析 → agent_s3.json
    │
    ▼
[run_hara.py s3 compose] ──► s3_hazop.json
    │
    ▼
[run_hara.py hara prepare] ──► s4_hara_skeleton.json
    │ (Domain Pack + 案例库 v2 候选)
    ▼
[Agent/工程确认 + hara autofill]
    │
    ▼
[run_hara.py hara validate] ──► s4_hara_final.json
    │ (仅验证通过才生成正式 ASIL/ID)
    ▼
[run_hara.py sg generate] ──► safety_goals.json
    │
    ▼
[run_hara.py write] ──► output/result.xlsx
```

**关键变化**：
- 案例库 v2 自动提供可审计候选，但不替代工程确认
- exact 仅在语义完整且同键 assessment 一致时锁定
- similar/template/冲突候选不得跳过 `hara validate`
- S5/write 只接受 `validation.passed=true` 的正式 final
    ▼
[run_hara.py sg generate] ──► safety_goals.json（整车安全目标汇总）
    │
    ▼
[run_hara.py write --s3 --s4 --s5] ──► 输出.xlsx（5 Sheet：功能清单 + 失效模式 + HAZOP + HARA + 整车安全目标）
```

## 关键规则速查

- **S1 安全侧原则**：Agent 未判定时默认保持"是"（宁可多做不遗漏）
- **S1 判断单位**：整个相关项，不是单个子功能
- **S2 默认值**：未知功能无默认；命中 exact Domain Pack 的功能由脚本锁定，Agent 不得改写
- **S3 覆盖范围**：覆盖 S2 选中的所有模式；枚举粒度必须遵循当前域 Profile，CS 转向按相关项级主要安全功能生成 5 个 entry，内部组件仅作追溯；一个异常可多危害；无危害也列出标"不涉及"
- **S3 写作规范**：异常 5-15 字到子功能级别，危害 ≤ 20 字声明式
- **S3 关联 HARA**：正常留空 `""`，"不涉及"的写 `"不涉及"`，S4 写入时由脚本回填 ID 范围
- **S3 质量标准**：详见 `references/s3-hazop-quality.md`（含好/坏对比 + 四数据组真实范例）
- **S4 ASIL 由脚本计算**：Agent 只确认/微调 S/E/C，不填 ASIL；ID 由脚本分配
- **S4 S/E/C 预填**：exact Domain Pack 先按稳定 ID 精确锁定；非精确矩阵域才使用参考数据库预填。Agent 只可处理未锁定事件
- **S4 sec_source 追溯**：每个 event 标注 S/E/C 来源（reference_exact/reference_pattern/agent_evaluated）
- **S4 标准场景集**：按危害类型组织（转向19场景、溜车12场景、制动9场景等），详见 `references/s4-hara-rules.md`
- **S4 场景库按域维护**：PT 场景单源位于 `references/domain_packs/PT.json`；CS/CB/AD/ET/BD 场景文件位于 `references/scenarios/`。PT 不得回退到旧总表或全局 SEC 库，CS/CB 的智能分支仍在对应域文件中维护。
- **S4 工程判断例外**：C1+低速+驾驶员在车内可判 QM（查表为 A），需填 engineering_override
- **S4 场景预填**：所有失效模式（含"不足/过大"类）均预填实际场景行并展开 S/E/C，不使用引用行
- **S5 必经步骤**：`hara validate` 后必须执行 `sg generate` 生成 `safety_goals.json`，`write` 必须带 `--s5`；全 QM 域（如 ET）输出 0 条安全目标属正常，但命令仍须执行
- **S5 质量闸门**：S5 error 会在写文件前阻断；应回到 S4 Agent 输入修正并重跑正式链，禁止直接改 S4 final/S5 或用 `--force` 绕过
- **S5 去重编号**：按安全目标文本精确去重，ASIL 取所有关联事件最高等级，整车级编号 `{域}_SG_VH_{4位流水}`（如 `P_SG_VH_0001`），按 ASIL 降序排列
- **章节匹配**：H2 分组 → 精确匹配（脚本完成）→ 未匹配项保留审计证据；若 PT HARA 功能级案例集已 exact 锁定，则不触发 Agent 文档语义推理，否则才按 document_parse_request 处理。
- **模板 Sheet 保护**：只修改"相关项功能清单""失效模式""HAZOP 分析""HARA 分析""整车安全目标"五个 Sheet 的数据区，不删除、不重命名、不修改任何其他 Sheet

## 注意事项

- 原项目 `hara-analysis-pipeline` 不做任何改动，本 Skill 为独立副本
- PowerShell 环境用 `;` 分隔命令，不支持 `&&`
- Python 环境：直接用 `python` 命令（系统 Python 3.12+，需安装 openpyxl、python-docx）
- 模板路径：`assets/template.xlsx`，write 命令默认自动查找
- 所有 Agent 可写输出放入 `output/` 目录，禁止修改 `scripts/`、`references/`、`assets/`

## 2026-08-25：正式发布链路与 Agent 边界（强制）

本节适用于 Aily 等调用方 Agent。Agent 负责工程判断，不负责改写运行时实现或绕开阶段合同。

1. **唯一可执行入口**：只调用 `scripts/run_hara.py` 的公开子命令。不得读取/修改 `scripts/stages/*.py`、不得通过 `grep`、源码阅读或 Pack 内部结构猜测字段合同，更不得创建 `create_events.py`、`fix_*.py`、`map_*.py` 等临时脚本批量修复 S2～S5 JSON。
2. **禁止伪造新 vehicle_hazard_id**：只能使用 Domain Pack 中已定义的 ID；Pack 中不存在的危害类别需工程师审批后更新 Pack。
3. **PT 正式链必须同链追溯**：PT 的 `hara validate` 必须传入本轮 `--s3 s3_hazop.json`。脚本会校验 `analysis_unit_id + failure_id + semantic_key`，因此不能保留旧 ID 而替换异常或整车危害语义。
4. **受控 S4 完成方式**：Agent 只能在 S4 骨架允许的非锁定字段内完成工程评定，然后调用 `hara validate` 验证；禁止手动修改 `semantic_key`，由脚本自动生成保证追溯一致性。

PT 推荐正式命令链：

```powershell
python -X utf8 scripts/run_hara.py hara prepare s3_hazop.json --intermediate intermediate.json --s1 s1_decisions.json --s2 s2_decisions.json -o s4_hara_skeleton.json
# Agent 填写/审核 events，保存为 s4_hara_agent.json
python -X utf8 scripts/run_hara.py hara validate s4_hara_agent.json --s3 s3_hazop.json -o s4_hara_final.json
python -X utf8 scripts/run_hara.py sg generate s4_hara_final.json -o safety_goals.json
python -X utf8 scripts/run_hara.py write intermediate.json s1_decisions.json s2_decisions.json --s3 s3_hazop.json --s4 s4_hara_final.json --s5 safety_goals.json -o output/result.xlsx
```

`write` 成功后会生成同名 `<result.xlsx>.run_manifest.json`。该文件记录运行时源码、域资产和 S1～S5/Excel 工件的 SHA-256，是正式交付的一部分；不得用不同轮次的中间产物拼接替代。
