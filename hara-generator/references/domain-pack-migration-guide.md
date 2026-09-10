# Domain Pack 域改造指南：指导 AI 将其他功能域纳入 HARA 精确分析体系

> **适用对象**：后续协助本项目的 AI / Agent，以及将 CB、PT、AD、ET、BD 或新域迁入本项目的工程师。  
> **当前样板**：CS（转向）Domain Pack。  
> **目标**：将历史跨域大参照表拆解为“统一框架 + 按域小 Pack + 分阶段合同”，使已批准的已知输入可稳定、可追溯、不可被 Agent 静默改写地完成 HARA。  
> **非目标**：复制某份参考 Excel 的格式、行号、单元格布局，或为某个数据组写特例。

---

## 1. 不可违反的原则

### 1.1 只处理五张目标 Sheet

运行时只处理下列 Sheet 的业务字段和内部关系：

```text
相关项功能清单
失效模式
HAZOP 分析
HARA 分析
整车安全目标
```

不得把其他 Sheet 的格式、公式、排版、历史记录当作 HARA 业务规则；写 Excel 时不得修改非目标 Sheet。

### 1.2 参考文件只用于离线沉淀语义

参考 DOCX / Excel 只能用于提炼：

- 功能、子功能、组件和章节的工程语义；
- S1、S2、S3、S4、S5 之间的内部关系；
- 已批准的失效模式、危害、场景、S/E/C、ASIL 和安全目标。

禁止：

- 依赖参考 Excel 的 Sheet 名以外的格式、行列位置、行号、合并单元格；
- 将 Feature List ID、文件名、数据组编号当作跨项目稳定的业务主键；
- 因某份参考文件出现某个文本，就对所有项目无条件套用；
- 以“参考文件本来就是这样填的”为理由保留不一致或不完整的历史结果。

### 1.3 已知、兼容、未知必须分流

| 状态 | 含义 | Agent 行为 |
|---|---|---|
| `exact_known` | 已有批准证据能确定分析范围和结果 | 脚本锁定；Agent 只能核对输入边界，不能改写 |
| `compatible` | 与已知功能相近但存在明确差异 | 脚本给受控基线；Agent 只处理差异并说明理由 |
| `unknown` / `needs_review` | 无充分证据或语义不清 | 必须明确工程复核；不得静默套用基线 |

不得为了输出完整而把 unknown 伪装成 exact_known。

### 1.4 不得把 CS 转向基线套到其他域

CS 只是样板。CB、PT、AD、ET、BD 必须各自沉淀：

```text
功能语义映射
标准分析单元
S1 范围规则
S2 失效模式合同
S3 HAZOP 模板
S4 场景风险矩阵
整车危害目录
S5 安全目标合并规则
```

在这些资产未完成前，其他域必须保持 `needs_review`，不能借用 CS 的场景、S/E/C、危害或安全目标。

---

## 2. 目标架构

### 2.1 统一入口与按域加载

```text
references/domain_packs/
├── manifest.json
├── schema.json
├── CS/
│   ├── CS.json
│   └── CS_subfunctions.json（部署该域子功能权威资产时）
├── CB/
│   └── CB.json
├── PT/
│   ├── PT.json
│   └── PT_subfunctions.json
├── AD/
│   └── AD.json
├── ET/
│   └── ET.json
└── BD/
    └── BD.json
```

运行时必须按下列方式工作：

```text
解析输入
→ 识别 domain
→ 读取 manifest 的别名映射
→ 仅加载当前 domain 的 Pack
→ 按 Pack 内合同生成与验证
```

不得要求 Agent 每次加载跨域大场景表、跨域 S/E/C 表或全量历史案例。

### 2.2 Domain Pack 的固定结构

每个域 Pack 都应使用 `schema.json` 约束，按职责拆分为：

| 区块 | 职责 |
|---|---|
| `function_catalog` | 功能别名、语义角色、输入匹配、范围规则 |
| `analysis_catalog` | 标准分析单元、S2 失效模式、S3 HAZOP 模板 |
| `risk_catalog` | S4 场景集合、场景匹配、精确风险事件矩阵 |
| `safety_goal_catalog` | 整车危害目录、危害族、安全目标规则 |
| `quality_contract` | 精确已知项、未知项策略、完整性和一致性合同 |

辅助的、跨项目可复用语义资产可放在：

```text
references/aliases/
references/profiles/
references/scenarios/
```

这些资产也必须按工程语义维护，不能保存某份参考 Excel 的格式依赖。

---

## 3. AI 的标准实施步骤

AI 必须遵循“先审计、后建模、再实现、最后回归”的顺序，不能先为当前数据组硬编码。

### 阶段 A：证据审计（先不改代码）

1. 明确目标域和域边界；
2. 收集一个以上优先多个已批准项目的：相关项定义 DOCX、HARA 输出/参考 Excel、工程评审结论；
3. 只提取五张目标 Sheet 的字段关系；
4. 建立语义证据表，至少包含：

```text
输入功能 / 子功能语义
章节、组件、执行器、控制对象证据
语义角色
是否属于当前域
是否进入 HARA
标准分析单元
失效模式
HAZOP 异常与 failure_id
整车危害
场景
S/E/C、ASIL
安全目标、安全状态、FTTI
```

5. 将结论分成“跨项目一致”“项目特有”“证据不足”；
6. 先报告冲突和缺口，再决定能否写入 Pack。

### 阶段 B：语义建模

1. 定义功能族和语义角色；使用工程语义命名，如 `brake_apply_control`，不使用项目编号作为语义主键；
2. 用功能名、子功能名、章节、组件、执行器、控制对象、别名建立匹配证据；
3. 定义标准分析单元（analysis unit）：多个 Feature 可覆盖到同一工程功能边界；
4. 明确 `covered_by`、`exclude`、`transfer_to_domain` 与 `needs_review` 的边界和理由；
5. 只有语义证据充足时才锁定，不确定项必须保留人工复核。

### 阶段 C：逐层沉淀 Pack

推荐顺序：

```text
1. function_catalog
2. analysis_catalog.analysis_units
3. analysis_catalog.failure_mode_rules
4. analysis_catalog.hazop_patterns
5. safety_goal_catalog.hazard_catalog
6. risk_catalog.scenario_sets / scenario_lookup
7. risk_catalog.event_matrix
8. quality_contract
```

每完成一层就增加测试和真实数据回归；不得在证据不足时一次性填满整个 Pack。

---

## 4. S1 至 S5 的改造要求

### 4.1 S1：HARA 范围判定

对每个已知 Feature 语义，至少沉淀：

```text
semantic_role
semantic_status
analysis_unit_id
disposition
covered_by / target_domain
reason_code
s1_rule_is_hara
```

最终 `s1_decisions.json` 必须满足：

```text
item.is_hara == any(sub_functions[].is_hara)
```

并且：

- 最终功能集合和 Feature 集合必须与 `intermediate.json` 一一对应；
- 已锁定的 `s1_rule_is_hara` 不得被 Agent 改写；
- 不允许父级和子功能的 `is_hara` 相互矛盾；
- Agent 原始 S1 只能用于 `merge`，不能作为下游最终输入直接手写；
- 已知项由 Pack 锁定，unknown 才交 Agent / 工程复核。

### 4.2 S2：失效模式

对每个 S1=`是`的标准分析单元，建立受控失效模式集合：

```text
analysis_catalog.failure_mode_rules
```

实施时应从批准数据中提炼模式、归并同义词、区分功能丧失、非预期、过大/过小、反向、卡滞等工程语义。

合同要求：

- S2 只能覆盖最终 S1=`是`的功能；
- S1=`否`的功能不得进入 S2；
- 已知分析单元的模式必须与 Pack 精确一致；
- 未知项可以提议模式，但必须有理由并标记复核。

### 4.3 S3：HAZOP

已知分析单元应在：

```text
analysis_catalog.hazop_patterns
```

中沉淀至少以下关系：

```text
analysis_unit / func_id
failure_mode
anomaly_description
failure_id
hazard_description
vehicle_hazard_id
hazard_family
```

合同要求：

- 每个 S2 选中模式必须在 S3 有唯一处置；
- 不得漏模式、增模式或产生重复功能+模式；
- 每个 `failure_id` 必须进入 S4，或者有受控的 skip 及工程理由；
- 已知项的危害文本与危害目录键必须匹配 Pack。

### 4.4 S4：场景、S/E/C、ASIL

已批准、稳定的已知输入必须沉淀为：

```text
risk_catalog.scenario_sets
risk_catalog.scenario_lookup
risk_catalog.event_matrix
```

每条精确风险事件至少应锁定：

```text
analysis_unit / failure_id
scenario_id / scenario
hazard / vehicle_hazard_id / hazard_family
severity / severity_reason
exposure / exposure_reason
controllability / controllability_reason
expected_asil
safety_goal / safe_state / ftti
```

规则：

1. `exact_known` 的 S/E/C、理由、ASIL、SG、安全状态、FTTI 均由 Pack 锁定，Agent 不可改写；
2. unknown 不得伪装为精确事件，必须进入 `needs_review`；
3. 统一执行：

```text
S = 0
→ E / E_reason = null
→ C / C_reason = null
→ ASIL = QM
→ safety_goal / safe_state / FTTI / safety_goal_id = null
→ 不进入 S5
```

4. 不得让旧跨域大表、模糊场景匹配或通用启发式覆盖当前域批准的精确矩阵。

### 4.5 S5：整车危害与安全目标

在 `safety_goal_catalog` 中维护：

```text
hazard_catalog
hazard_families
rules
```

字段职责固定：

| 字段 | 含义 |
|---|---|
| `vehicle_hazard_id` | 稳定整车危害目录 ID，用于 S3/S4 展示与追溯 |
| `hazard_id` | 单条 HARA 风险事件实例 ID |
| `hazard_family` | S5 合并用的风险语义分类 |

安全目标建议按以下语义键合并：

```text
domain + hazard_family + safety_goal + safe_state + ftti
```

不同危害族、不同安全状态或不同 FTTI 的目标不得仅因中文文案相近而合并。

---

## 5. 强制执行的阶段链路

```powershell
# 解析
python scripts/run_hara.py parse <input.docx> -o intermediate.json

# S1：合并 Pack 锁定结论与 Agent 原始判断
python scripts/run_hara.py merge intermediate.json agent_s1.json -o s1_decisions.json

# S2
python scripts/run_hara.py s2 generate intermediate.json s1_decisions.json -o s2_decisions.json
python scripts/run_hara.py s2 validate s1_decisions.json s2_decisions.json --intermediate intermediate.json

# S3
python scripts/run_hara.py s3 generate intermediate.json s1_decisions.json s2_decisions.json -o s3_hazop.json
python scripts/run_hara.py s3 validate s1_decisions.json s2_decisions.json s3_hazop.json --intermediate intermediate.json

# S4
python scripts/run_hara.py hara prepare s3_hazop.json --intermediate intermediate.json --s1 s1_decisions.json --s2 s2_decisions.json -o s4_hara_skeleton.json
python scripts/run_hara.py hara validate s4_hara_skeleton.json --s3 s3_hazop.json -o s4_hara_final.json

# S5
python scripts/run_hara.py sg generate s4_hara_final.json -o safety_goals.json

# 写入 Excel（仅五张目标 Sheet）
python scripts/run_hara.py write intermediate.json s1_decisions.json s2_decisions.json --s3 s3_hazop.json --s4 s4_hara_final.json --s5 safety_goals.json -o result.xlsx
```

不可绕过的边界：

- Agent 原始 S1 必须先经过 `merge`；
- `hara prepare` 必须传入 `--intermediate`、`--s1`、`--s2`；
- `hara prepare` 必须先验证最终 S1 合同，再处理空 S3；
- `write --force` 不能绕过 S1/S2/S3/S4 硬校验；
- 任一验证失败，必须停止，不得写正式 Excel；
- 回归产物只能写入临时目录，不得覆盖输入、参考文件或 `E:\backup\hara-generator-skill`。

---

## 6. 新域的里程碑和验收

### M0：域边界确认

交付：域范围、输入/参考数据清单、跨域移交项、已知/兼容/未知清单。  
验收：不会把明显属于其他域的功能写入本域 Pack。

### M1：语义映射与 S1 合同

交付：`function_catalog`、分析单元、`s1_rule_is_hara`、必要的 aliases/profiles、S1 测试。  
验收：已知“是/否/移交”结论不依赖 Feature ID、文件名或数据组编号。

### M2：S2/S3 合同

交付：失效模式规则、HAZOP 模板、范围/完整性/覆盖测试。  
验收：每个 S1=`是`功能都有完整、无重复、无越界的 S2/S3 处置。

### M3：整车危害与 S4 基线

交付：危害目录、危害族、场景集、风险矩阵初稿。  
验收：每个 failure_id 都有 HARA 事件或受控 skip，不存在静默漏项。

### M4：精确风险矩阵

交付：`event_matrix`、逐字段 S/E/C/ASIL/SG/FTTI 合同、S=0→ASIL 留空 规则。  
验收：只有证据充分、跨项目稳定的集合才标记为 `exact_known`。

### M5：端到端回归

至少测试：

```text
正常输入
命名变体
篡改最终 S1
篡改 Pack 锁定值
空 S2/S3 绕过
S2 越界
S3 漏模式
S4 漏 failure_id
S=0→ASIL 留空
错误 vehicle_hazard_id
真实模板写入
其他已稳定域不回归
```

---

## 7. AI 必须报告、不得静默处理的情况

以下情况必须报告现象、证据、影响范围、建议状态和所需人工决策：

1. 多个批准项目对同一语义给出冲突的 S/E/C、ASIL 或安全目标；
2. 参考项目的 HAZOP/HARA 缺项，无法确认是允许 skip 还是历史错误；
3. 一个功能可能属于两个域；
4. 仅有单一项目证据，不能判断是否可泛化；
5. 同名功能具有不同执行器、控制对象或车辆状态；
6. 结论只能通过 Feature ID、文件名或数据组编号识别；
7. 新规则可能影响 CS 或其他已稳定域的回归结果。

不确定时，正确行为是标记 `needs_review` 并请求工程决策，而不是猜测一个看似完整的答案。

---

## 8. “100% 正确”的边界

“100% 正确”只能针对：

> 已由批准工程证据沉淀为 `exact_known`，且已由精确风险矩阵逐字段锁定并完成回归的输入集合。

它不等于对所有未来项目、未知功能、语义模糊输入或尚未迁入的新域都能自动正确。

一个域的 exact_known 结论至少必须满足：

```text
[ ] S1 逐 Feature 正确
[ ] S2 模式逐项正确
[ ] S3 failure_id 完整
[ ] S4 场景、危害、S/E/C、理由、ASIL、SG、safe state、FTTI 正确
[ ] S=0 严格为 ASIL 留空 且不进入 S5
[ ] vehicle_hazard_id 正确
[ ] S5 合并正确
[ ] 篡改与跳过链路均被阻断
[ ] 真实模板写入后五张 Sheet 的内部关系正确
[ ] 不破坏已稳定域
```

---

## 9. 每次改造完成后 AI 必须交付

1. 改动文件清单及原因；
2. 新增/更新的 Pack 资产说明；
3. 未采纳的参考特例及原因；
4. 自动化测试和真实数据回归命令、结果；
5. 负向测试：篡改和绕过是否被阻断；
6. 对其他域的影响检查；如有影响先报告方案，不要未经确认修改稳定规则；
7. 遗留的未知项和人工决策点；
8. 更新 `PROJECT_STATUS.md`：日期、目标/根因、实现、验证、未完成项。

---

## 10. 新域改造前自检清单

```text
[ ] 只分析五张目标 Sheet 的关系，不分析参考 Excel 格式。
[ ] 已确认当前 domain，运行时不会加载或套用无关域的大表。
[ ] 没有把 Feature ID、文件名、数据组编号当作业务语义主键。
[ ] 已区分 exact_known、compatible、unknown。
[ ] 已知结论进入当前域 Pack，而不是散落为脚本硬编码。
[ ] 新规则有正向、负向和跨域回归测试。
[ ] 按 intermediate → merge → S2 → S3 → S4 → S5 → write 的合同链路工作。
[ ] 不覆盖原始输入、参考文件或 E:\backup\hara-generator-skill。
[ ] 更新 PROJECT_STATUS.md，并明确未知项。
```

---

## 11. 当前基线

- **CS（转向）**：精确样板。当前已具备标准分析单元、5 种失效模式、5 条 HAZOP、19 个场景、95 条精确 HARA 风险事件、5 个整车危害目录项、2 条整车安全目标，以及 S1→S5 合同防绕过机制。
- **CB / PT / AD / ET / BD**：已具备统一 Schema 和独立 Pack 骨架；尚未全部沉淀为 CS 同等级的精确分析资产。未完成迁入和验证前必须走 `needs_review`，不得宣称精确自动判定。

## 结论

正确的扩展方向不是继续扩充一张跨域大表，而是：

```text
统一 Schema
+ 当前域独立 Pack
+ 语义映射而非项目 ID
+ 标准分析单元
+ 分阶段合同
+ 已知项精确风险矩阵
+ unknown / needs_review 工程边界
```

这样每个新域都能复用 CS 的流程、校验器和写入链路，而不会把某个参考项目的偶然格式或历史填写方式固化为系统规则。
