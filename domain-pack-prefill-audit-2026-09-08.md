# HARA Domain Pack 预填能力审查报告

**审查日期：** 2026-09-08  
**目标项目：** `E:\Git projects\hara-generator`  
**待审查资产目录：** `C:\Users\zhouguangjian\Downloads\domain_packs(1)\domain_packs`  
**覆盖域：** CS、PT、此前已审查的 CB  
**审查方式：** 结构校验、来源/引用一致性检查、子功能权威表加载、案例库加载、语义功能解析、案例精确匹配和不完整输入匹配。

---

## 1. 最终结论

### 1.1 总体结论

这批资产的质量不是三种域完全相同：

| 域 | 完整 Domain Pack 校验 | 子功能权威表 | 案例库读取 | 能否直接用于正式预填 |
|---|---:|---:|---:|---:|
| CS | 通过 | 通过，但有来源与功能解析问题 | 171 条可加载 | **不建议直接正式投入** |
| PT | 通过 | 通过，但存在别名、未解析案例和完整性问题 | 490 条可加载 | **不建议不加控制地正式投入** |
| CB | 不通过 | **空文件，不能支撑 S1** | 46 条可作为临时案例库加载 | **不能直接投入** |

CS 和 PT 已经比之前的 CB 资产完整得多：它们具备 `function_catalog`、`analysis_catalog`、`risk_catalog` 和 `safety_goal_catalog`，并且能通过项目当前的 `validate_domain_pack` 校验。

但是，“能通过结构校验”不等于“可以无条件锁定预填结果”。CS、PT 仍然存在以下会影响预填可信度的问题：

- 部分案例缺少整车安全目标；CS/PT 中另有部分 `severity=0` 记录属于项目合同定义的“无整车层面危害”分支，不应误判为普通 S/E/C/ASIL 字段缺失；
- CS 的 81 条案例没有映射到整车安全目标；
- PT 的 271 条案例没有映射到整车安全目标；
- PT 有 20 条案例缺少场景和危害，另有 1 条案例完全没有可解析的功能族；
- PT 有 18 条案例被归为 `legacy_anomaly_`，其中至少一部分是异常语义为空或未识别；
- CS/PT 的子功能文件与运行时案例的语义颗粒度没有完全打通；
- CS/PT 的来源路径和来源哈希存在可复现性问题；
- 当前项目的外部路径切换机制只对 PT 生效，CS 和 CB 不会因为设置 `HARA_DOMAIN_PACKS_ROOT` 自动切换到外部目录。

因此，建议将这批文件定义为：

> **可作为候选外部资产和参考预填资产使用，但必须保留 `needs_review` / 可编辑降级机制，不能把所有 `approved_reference` 记录直接视为可锁定的正式 HARA 结论。**

---

## 2. 检查文件清单

### 2.1 CS

- `C:\Users\zhouguangjian\Downloads\domain_packs(1)\domain_packs\CS\CS.json`
- `C:\Users\zhouguangjian\Downloads\domain_packs(1)\domain_packs\CS\CS_subfunctions.json`

统计结果：

```text
CS.json：171 条案例
CS_subfunctions.json：22 条子功能记录，2 个相关项功能
```

### 2.2 PT

- `C:\Users\zhouguangjian\Downloads\domain_packs(1)\domain_packs\PT\PT.json`
- `C:\Users\zhouguangjian\Downloads\domain_packs(1)\domain_packs\PT\PT_subfunctions.json`

统计结果：

```text
PT.json：490 条案例
PT_subfunctions.json：90 条子功能记录，8 个相关项功能
```

### 2.3 CB 对照结果

- `C:\Users\zhouguangjian\Downloads\CB_domain_pack\CB.json`
- `C:\Users\zhouguangjian\Downloads\CB_domain_pack\CB_subfunctions.json`
- 源文件：`C:\Users\zhouguangjian\Downloads\CB_BRAKE_EPB_提取结果.xlsx`

统计结果：

```text
CB.json：46 条案例，但 case_count 错误写成 0
CB_subfunctions.json：0 条记录，而源 Excel 实际有 8 条子功能
```

---

## 3. 运行时验证结果

在项目 `E:\Git projects\hara-generator` 中，使用当前代码执行：

```python
validate_domain_pack(data, expected_domain=domain)
```

结果：

```text
CS：0 个结构错误
PT：0 个结构错误
CB：9 个结构错误
```

CS 和 PT 因此具备进入“候选运行时资产”阶段的结构条件。CB 仍然不能作为完整 Domain Pack 加载。

需要特别注意，当前的案例库加载器会直接读取：

```text
case_library_catalog.cases
```

所以即使一个文件不是完整 Domain Pack，仍有可能被 `CaseLibrary` 当作案例库加载。这解释了为什么 CB 可以加载 46 条案例，但仍然不能支撑完整的 S1-S5 流程。

---

# 4. CS 审查结果

## 4.1 CS 的优点

CS 文件的整体结构是当前三份资产中最完整、最干净的一类：

```text
function_catalog：存在
analysis_catalog：存在
risk_catalog：存在
safety_goal_catalog：存在
quality_contract：存在
case_library_catalog.case_count：171，和实际案例数一致
```

CS 的运行时核心配置包括：

```text
功能族：steering_assist
分析单元：CS_STEERING_ASSIST_MAIN
场景目录：19 条
风险事件矩阵：171 条
危害目录：7 条
整车安全目标：2 条
案例：171 条
```

使用 CS Pack 对典型输入进行功能解析：

```text
输入：转向助力功能
结果：
  status = resolved
  canonical_function_family = steering_assist
  analysis_unit_id = CS_STEERING_ASSIST_MAIN
  disposition = analyze
  confidence = 1.0
```

使用案例库对完整案例语义进行匹配，也可以得到：

```text
match_type = exact
confidence = 1.0
候选数 = 1
```

所以 CS 在“结构完整、功能族解析、完整案例精确匹配”方面是可以工作的。

---

## 问题 CS-1：CS 有 81 条案例没有整车安全目标映射

CS 共 171 条案例，其中：

```text
有 vehicle_safety_goal_id：90 条
没有 vehicle_safety_goal_id：81 条
```

但这些案例仍然标记为：

```text
"evidence_status": "trusted_reference"
```

这意味着它们可以作为可信参考案例被读取，但不能完整支撑 S5 安全目标预填。

### 影响

当输入命中这些案例时，系统可以预填：

- 功能族；
- 失效模式；
- 危害；
- 场景；
- 部分 S/E/C/ASIL；

但无法可靠预填：

- 整车安全目标 ID；
- 安全目标文本；
- 安全状态；
- FTTI 的完整关联。

如果上层代码把 `trusted_reference` 误认为“所有字段均可直接锁定”，会产生不完整的 S5 结果。

### 解决方式

对 CS 案例执行以下处理：

1. 对有明确危害但没有安全目标的案例，重新根据 CS 的 `hazard_event_to_goal` 和 `semantic_candidates` 做映射；
2. 如果无法唯一映射，不要猜测，保持空值并设置：
   ```text
   evidence_status = needs_review
   ```
3. 只有同时具备以下字段时，才允许进入正式 exact 预填：
   ```text
   vehicle_hazard_class
   vehicle_safety_goal_id
   safety_goal
   safe_state
   ftti
   ```
4. 重新生成 `vehicle_safety_goal_catalog.case_to_goal`，确保它覆盖实际允许映射的 CS 案例，而不是仅覆盖 90 条已有映射记录。

---

## 问题 CS-2：CS 存在 16 条 severity=0 的非危害记录，但不是普通评估字段缺失

CS 中有 16 条案例的：

```text
severity = 0
exposure = null
controllability = null
asil = null
```

按照当前项目的 exact assessment 合同，`severity=0` 表示车辆层面没有可分析危害，S/E/C/ASIL 留空是允许的。这 16 条记录并不属于普通的 assessment 不完整案例。

### 影响

这些记录不能用于锁定车辆层面的 S/E/C/ASIL 或安全目标，但可以作为“无整车层面危害”的参考记录保留。

真正需要检查的是：这些记录是否都明确带有 S0 理由、是否与源 Excel 的“不涉及/无危害”语义一致，以及是否错误地标记为可以生成安全目标。

### 解决方式

1. 保留 `severity=0` 的特殊合同，不要把数值 0 当成缺失值；
2. 对 severity=0 记录增加或验证：
   ```text
   severity_reason
   safety_goal = null
   vehicle_safety_goal_id = null
   ```
3. 在报告和校验脚本中使用 `is None` 判断字段缺失，不要使用简单的真假判断；
4. 将这类记录标记为 `not_applicable` 或等价状态，避免被 S5 安全目标流程误处理；
5. 本次检查确认 CS 的 171 条案例全部满足当前项目的 assessment exact 完整性合同，问题主要是安全目标映射，而不是 S/E/C/ASIL 解析失败。

## 问题 CS-3：CS 的安全机制功能在功能语义解析器中无法直接解析

CS 子功能表中有 4 条：

```text
功能名称：安全监控及故障报警功能
HARA判定：否
```

但是使用 CS 的 `function_catalog` 直接解析：

```text
输入：安全监控及故障报警功能
结果：unknown / needs_review
```

CS 的功能匹配器只定义了：

```text
转向助力功能
转向助力
```

没有为“安全监控及故障报警功能”配置显式的 `safety_mechanism` 语义角色或排除规则入口。

### 影响

如果 S1 流程仅依赖 `function_catalog`，而没有先读取 `CS_subfunctions.json`，则安全机制子功能可能被错误标记为：

```text
unknown
needs_review
```

而不是明确的：

```text
exclude
```

### 解决方式

1. 保持 `CS_subfunctions.json` 作为 S1 的权威来源；
2. 在 `function_catalog.semantic_roles` 中增加安全机制关键词，例如：
   ```text
   安全监控
   故障报警
   过热保护
   过欠压保护
   ```
3. 将其映射到：
   ```json
   {
     "semantic_role": "safety_mechanism",
     "disposition": "exclude",
     "reason_code": "SAFETY_MECHANISM"
   }
   ```
4. S1 结果必须保留：
   ```text
   hara = false
   hara_source = CS_subfunctions.json
   hara_source_row
   hara_source_record_id
   ```

不能因为功能解析器返回 unknown，就覆盖掉子功能权威表中已经明确的“不进行 HARA”结论。

---

## 问题 CS-4：CS 子功能文件的来源哈希与当前资产不一致

CS Pack 中记录的来源信息是：

```text
source_file = /home/gem/.aily/workspace/references/domain_packs/CS/CS.json
source_sha256 = 277b42bf...
```

但当前下载文件 `CS.json` 的文件哈希是：

```text
37cb1a25243f79a439c1e1700b0ea9148454f9fe96ae98be9fbcd9fbc687ac7c
```

这说明当前文件不是按照其自身 provenance 中声明的文件内容构建的，或者中间经过了重新生成但没有更新 provenance。

### 影响

这不会一定阻止功能预填，但会影响：

- 资产可复现性；
- 审计追踪；
- 是否能证明子功能表和案例包来自同一批源数据；
- 外部工作区部署后的问题定位。

### 解决方式

重新构建 CS 资产时：

1. `source_file` 使用实际源文件或稳定的相对来源标识；
2. `source_sha256` 按实际源文件重新计算；
3. `CS_subfunctions.json` 和 `CS.json` 必须使用同一批构建输入；
4. 不要把生成机的 `/home/gem/.aily/...` 临时路径作为唯一来源地址；
5. 追加：
   ```json
   "source_path_at_build": "..."
   ```
   但运行时不依赖这个绝对路径。

---

# 5. PT 审查结果

## 5.1 PT 的优点

PT 是当前三份资产中内容最丰富的一份，整体运行时结构也通过校验：

```text
function_catalog：8 个功能族
analysis_catalog：6 个分析单元
risk_catalog：431 条运行时事件矩阵
reference_event_matrix：443 条参考事件
scenario_catalog：248 条场景
safety_goal_catalog：37 个危害族
vehicle_safety_goal_catalog：21 个安全目标
case_library_catalog：490 条案例
```

PT 的 8 个功能族和案例分布基本合理：

| 功能族 | 案例数 |
|---|---:|
| pt_traction_torque_control | 151 |
| pt_gear_state_control | 105 |
| pt_hv_safety | 89 |
| pt_hv_power_state_management | 64 |
| pt_thermal_management | 53 |
| pt_charge_discharge | 26 |
| pt_energy_range_display | 1 |
| 未解析功能族 | 1 |

典型 PT 功能解析可以达到：

```text
档位控制及显示功能 -> pt_gear_state_control -> PT_GEAR_STATE_CONTROL_MAIN
整车高压上下电管理 -> pt_hv_power_state_management -> PT_HV_POWER_STATE_MANAGEMENT_BASELINE
车辆热管理功能 -> pt_thermal_management -> PT_THERMAL_MANAGEMENT_BASELINE
车辆扭矩控制 -> pt_traction_torque_control -> PT_TRACTION_TORQUE_CONTROL_MAIN
充放电功能 -> pt_charge_discharge -> PT_CHARGE_DISCHARGE_MAIN
高压安全 -> pt_hv_safety -> PT_HV_SAFETY_MAIN
车辆能耗及续航显示功能 -> pt_energy_range_display -> exclude
```

这些结果的置信度均为 1.0，说明 PT 的主要功能族设计是可以支撑基础 S1/S2/S3 路由的。

完整案例语义匹配也可以得到：

```text
match_type = exact
confidence = 1.0
候选数 = 1
```

所以 PT 不是“不能用”，而是需要在正式使用时处理好未解析、未完成和无安全目标的记录。

---

## 问题 PT-1：PT 存在 1 条完全未解析功能族的案例

案例：

```text
PT_CASE_79D7D38015E4
```

其关键字段为：

```json
{
  "canonical_function_family": null,
  "semantic_roles": [],
  "failure_profile": {
    "canonical_mode": "丢失",
    "anomaly_class": "legacy_anomaly_"
  },
  "hazard_profile": {
    "vehicle_hazard_class": null
  },
  "scenario_profile": {
    "scenario_text": ""
  },
  "evidence_status": "unresolved_semantics"
}
```

来源是：

```text
raw_function_id = P_func_0008
raw_failure_id = P_MF_0008_01
```

### 影响

该案例无法用于：

- 功能族匹配；
- 分析单元路由；
- S2 失效模式预填；
- S3 HAZOP 规则选择；
- S4 场景精确匹配。

当前匹配器对它会返回：

```text
none
```

### 解决方式

必须对 `P_func_0008` 做人工语义确认：

1. 如果它确实属于增程控制，应补齐：
   ```text
   canonical_function_family = pt_range_extender
   ```
   并增加对应分析单元和 HAZOP/风险规则；
2. 如果它属于 PT 当前范围外功能，应明确：
   ```text
   disposition = transfer / exclude
   ```
   并记录目标域或排除理由；
3. 如果源数据本身无法确认，保留：
   ```text
   evidence_status = unresolved_semantics
   ```
   但不要继续把它混在可用的 489 条可信案例中。

---

## 问题 PT-2：PT 的增程功能族有 matcher，但没有完整分析单元

PT 的 `function_catalog` 定义了：

```text
pt_range_extender
```

并且 `function_matchers` 中有：

```text
增程控制系统
增程器
增程模式
高压油箱
```

但是 `analysis_catalog.analysis_units` 中没有对应的：

```text
PT_RANGE_EXTENDER_...
```

直接解析“增程控制系统”时，结果会受到其他相似 matcher 的干扰，返回：

```text
status = needs_review
canonical_function_family = pt_range_extender
analysis_unit_id = null
```

### 影响

即使输入功能名能够识别为增程控制，也无法继续进入完整的标准分析单元。

### 解决方式

二选一：

#### 方案 A：正式支持增程功能

补充：

```text
analysis_unit
failure_mode_rules
hazop_patterns
scenario_set
event_matrix
hazard/safety-goal rules
```

并将 `pt_range_extender` 的 matcher 设置为唯一、优先命中。

#### 方案 B：暂不支持增程功能

从正式可用的 `function_matchers` 中移除或降低其状态，明确返回：

```text
needs_review / transfer
```

不能保留一个看似可解析、实际没有分析单元的半成品功能族。

---

## 问题 PT-3：PT 有 18 条 `legacy_anomaly_` 案例

PT 的案例中有 18 条：

```text
failure_profile.anomaly_class = legacy_anomaly_
```

这些记录主要分布在：

```text
pt_traction_torque_control：11 条
pt_hv_safety：4 条
pt_thermal_management：1 条
pt_energy_range_display：1 条
未解析功能族：1 条
```

### 影响

`legacy_anomaly_` 不是具有工程含义的失效/异常类别，不能用于：

- 精确案例匹配；
- HAZOP 模板选择；
- 风险事件矩阵键匹配；
- 自动生成安全目标。

### 解决方式

1. 如果原始功能异常表现存在，重新运行异常归一化规则；
2. 如果原始文本确实为空，将其设为：
   ```text
   anomaly_class = null
   evidence_status = needs_review
   ```
   不要把空字符串归一化为 `legacy_anomaly_`；
3. 只有经过人工确认的异常类别，才允许进入 `failure_mode_rules` 或 `event_matrix`；
4. 这些案例可以保留在参考库中，但不能作为 exact 预填来源。

---

## 问题 PT-4：PT 有 20 条案例缺少危害和场景

统计结果：

```text
缺少 vehicle_hazard_class：20 条
缺少 scenario_text：20 条
缺少 scenario semantic_fingerprint：20 条
```

这 20 条中有一部分的来源事件是：

```text
raw_hazard_event_id = 不涉及
```

因此不应强行推断其属于 HARA 风险案例。

### 影响

这些案例最多只能用于：

- 保留原始参考记录；
- 识别某些功能/失效的存在；
- 作为待补齐数据。

不能用于完整的 S4 HARA 风险事件预填。

### 解决方式

1. 将无危害、无场景的记录标记为：
   ```text
   evidence_status = reference_only / needs_review
   ```
2. 对 `raw_hazard_event_id = 不涉及` 的记录，不生成虚构的危害和场景；
3. 如果确实是 HARA 事件，回到源 Excel 补齐：
   ```text
   危害事件
   整车危害
   运行场景
   S/E/C/ASIL
   ```
4. 只有 `hazard_profile` 和 `scenario_profile` 完整时，才允许进入 exact 案例匹配。

---

## 问题 PT-5：PT 有 20 条案例的 assessment 不满足 exact 合同，另有 30 条 severity=0 特殊记录

PT 的原始空值统计容易误导，因为其中 30 条是：

```text
severity = 0
exposure = null
controllability = null
asil = null
```

这 30 条按照当前项目合同属于“无整车层面危害”的特殊记录，不是普通 assessment 缺失。

去除这 30 条后，PT 实际有 20 条案例不满足 exact assessment 合同。这 20 条主要缺少 severity 或属于完全未解析的参考记录。

### 影响

- severity=0 记录不能生成车辆安全目标，但可以作为明确的 `not_applicable` 参考记录；
- 真正不完整的 20 条记录不能直接锁定 S/E/C/ASIL；
- 如果生成器把 0 当成 false，会把合法的 S0 记录错误标记为缺失；
- 如果生成器把 `legacy_anomaly_`、空场景或空危害记录标记为可信案例，会造成过度预填。

### 解决方式

1. 使用项目合同判断 assessment 是否完整：
   ```text
   severity=0 且其他评估字段按规则为空 => 合同完整，但不适用 S/E/C/ASIL 锁定
   severity>0 => 必须具备 E/C/ASIL 及其理由
   ```
2. 对真正不完整的 20 条案例标记 `needs_review`；
3. 对 severity=0 记录标记 `not_applicable`，不参与安全目标匹配；
4. 不要用简单的 `if not value` 判断 S/E/C/ASIL 缺失，必须区分 `0` 和 `None`；
5. 只有通过 `_assessment_complete_for_exact` 等价合同的案例，才允许进入 exact 锁定候选。

## 问题 PT-6：PT 有 271 条案例没有整车安全目标

PT 共 490 条案例，其中：

```text
有 vehicle_safety_goal_id：219 条
没有 vehicle_safety_goal_id：271 条
```

其中 20 条属于 `raw_hazard_event_id = 不涉及`，但即使排除这 20 条，仍有 251 条带有危害事件或风险语义的案例没有安全目标映射。

### 影响

PT 案例库可以用于 S1-S4 的部分语义参考，但不能将这 271 条案例直接当作完整 S5 预填结果。

### 解决方式

1. 对有 `hazard_event_id`、有 `vehicle_hazard_class` 的案例，按 `hazard_event_to_goal` 重新检查映射；
2. 如果一个危害对应多个安全目标，必须通过 `semantic_candidates` 返回候选并降级为人工确认；
3. 如果没有安全目标定义，不能从案例文本中自由生成一个新的安全目标 ID；
4. 对 `不涉及` 或非 HARA 记录，明确设置：
   ```text
   safety_goal_status = not_applicable
   ```
5. 重新核对：
   ```text
   vehicle_safety_goal_catalog.goals
   case_to_goal
   hazard_event_to_goal
   semantic_candidates
   ```
   四者必须引用同一套 PT 安全目标 ID。

---

## 问题 PT-7：PT 子功能记录使用了 P 域别名，顶层域却是 PT

`PT_subfunctions.json` 的顶层域是：

```text
"domain": "PT"
```

但 90 条记录的 `domain` 值是：

```text
"P"
```

当前项目加载器可以通过别名归一化兼容这一点，当前测试可以成功加载。但是对于审计、跨工具交换和严格 schema 校验，这会造成不一致。

### 解决方式

统一采用：

```text
"domain": "PT"
```

同时保留 `P` 仅作为输入别名，不要在正式运行时记录中混用。

如果必须兼容历史 P 标识，应增加明确字段：

```json
{
  "domain": "PT",
  "legacy_domain_alias": "P"
}
```

而不是直接把记录域写成 P。

---

## 问题 PT-8：PT 来源路径指向另一个项目，影响可复现性

PT 的 provenance 指向：

```text
E:\Git projects\hara-generator-skill\结果整合\案例更新表_PT_整合_最终.xlsx
```

而当前实际运行项目是：

```text
E:\Git projects\hara-generator
```

这不一定代表 PT 内容错误，但会导致：

- 新机器无法按 provenance 找到源文件；
- 当前项目无法验证资产是否由同一源数据生成；
- 资产来自哪个版本不清晰；
- 将外部资产部署到 agent workspace 后难以审计。

### 解决方式

1. provenance 中同时保存：
   ```text
   source_file：逻辑来源名称
   source_path_at_build：构建机路径，可选
   source_sha256：实际源文件哈希
   source_version：源数据版本或提交号
   ```
2. 运行时不依赖绝对 Windows 路径；
3. 将 PT 源 Excel 或其不可变构建产物放入外部资产包并记录哈希；
4. 重新生成 `PT.json` 和 `PT_subfunctions.json` 时，使用同一份源文件和同一构建版本。

---

# 6. CB 审查结果整合

## 问题 CB-1：CB.json 缺少完整运行时 Domain Pack 结构

`CB.json` 缺少：

```text
function_catalog
analysis_catalog
risk_catalog
safety_goal_catalog
```

项目校验返回 9 个错误，因此 CB 不能作为完整 Domain Pack 加载。

### 解决方式

按照项目的：

```text
E:\Git projects\hara-generator\references\domain_packs\schema.json
```

补齐 CB 的：

1. 功能匹配器；
2. 语义角色；
3. 分析单元；
4. 失效模式规则；
5. HAZOP 模板；
6. 场景集合和场景查找；
7. 精确风险矩阵；
8. 危害目录；
9. 危害族；
10. 安全目标规则。

不能只把 `case_library_catalog` 包进一个 JSON 就当成完整 Domain Pack。

---

## 问题 CB-2：CB_subfunctions.json 是空文件

源 Excel 有 8 条 CB 子功能，但：

```text
CB_subfunctions.json.record_count = 0
CB_subfunctions.json.records = []
```

### 解决方式

从：

```text
C:\Users\zhouguangjian\Downloads\CB_BRAKE_EPB_提取结果.xlsx
```

的 `子功能提取` Sheet 重新生成 `CB_subfunctions.json`，至少应包含：

```text
record_count = 8
function_count = 1
conflict_count = 0
records = 8 条
```

其中“车辆特殊模式”的 HARA 判定应保持为“否”，不能因为生成器缺少记录而丢失排除结论。

---

## 问题 CB-3：CB 顶层功能族合理，但子功能语义没有进入案例匹配

CB 的 46 条案例全部来自：

```text
功能ID：CB_func_0008
功能名称：电子驻车制动
```

因此：

```text
电子驻车制动
```

作为顶层功能族是合理的，不建议简单拆成多个顶层功能族。

但当前所有案例的：

```text
semantic_roles = ["电子驻车制动"]
controlled_objects = []
capability_tokens = []
```

没有建立与下列子功能的关联：

```text
静态拉起，实现驻车
静态拉起，实现驻车解除
EPB内部驻车逻辑功能
动态制动
外部请求响应
液压支持HPS
驻车制动辅助保持
```

### 解决方式

保留一个顶层功能族：

```text
电子驻车制动
```

同时给案例增加明确的子功能语义或能力标签，例如：

```json
{
  "canonical_function_family": "电子驻车制动",
  "semantic_roles": ["动态制动"],
  "capability_tokens": ["动态驻车制动"]
}
```

不要把“动态制动”“驻车制动不足”“非预期释放”等失效或异常类别误当成顶层功能族。

---

## 问题 CB-4：CB 文件混入 CS 来源和 CS 风险数据

CB 的 provenance 指向 CS：

```text
source_file = /home/gem/.aily/workspace/references/domain_packs/CS/CS.json
source_case_count = 171
```

并且：

```text
vehicle_safety_goal_catalog 中混入 CS_VSG
case_to_goal 全部是 CS 映射
hazard_event_to_goal 全部是 CS 映射
semantic_candidates 全部是 CS 映射
exact_risk_matrix 使用 CS_STEERING_ASSIST_MAIN
```

### 解决方式

重新生成 CB Pack 时必须：

1. 删除所有 `CS_*` 目标、案例和事件映射；
2. 删除所有 `CS_STEERING_ASSIST_MAIN`、`CS_*_MF` 等风险矩阵引用；
3. 仅保留 CB 的 6 个安全目标；
4. 重新生成 CB 的 `case_to_goal`；
5. 重新生成 CB 的 `hazard_event_to_goal`；
6. provenance 指向 CB 实际源文件；
7. `source_case_count` 改为 46。

---

## 问题 CB-5：CB 案例完整性不足

需要重点复核：

```text
CB_CASE_000013：severity=0 的无危害记录，assessment 合同完整，但没有整车安全目标（这是预期的非适用分支）
CB_CASE_000046：缺少 severity、S/E/C/ASIL、危害和整车安全目标
```

另外有 11 条案例没有 `vehicle_safety_goal_id`。

### 解决方式

1. 从原始 Excel 复核这些案例是否本来就未填写；
2. 对 CB_CASE_000013 保留 severity=0 / not_applicable 语义，不要把 0 当成缺失；
3. 未填写的内容不能通过相似案例自动猜测；
4. 将真正不完整案例标记为：
   ```text
   needs_review
   ```
5. 只有字段完整且 assessment 一致的案例才允许 exact 锁定；
6. 当前案例匹配器对 CB_CASE_000046 已经会将结果降级为 similar，这个保护行为应保留。

---

# 7. 外部路径接入问题

当前项目的：

```text
E:\Git projects\hara-generator\scripts\utils\domain_packs.py
```

目前只对 PT 开启严格外部模式：

```text
HARA_DOMAIN_PACKS_ROOT 只对 PT 生效
```

实测在设置：

```text
HARA_DOMAIN_PACKS_ROOT=C:\Users\zhouguangjian\Downloads\domain_packs(1)\domain_packs
```

时：

```text
PT：从外部目录解析
CS：仍然从项目安装目录解析
CB：仍然从项目安装目录解析
```

## 解决方式

建议分阶段处理：

### 第一阶段：先清洗资产

先完成本报告中 CS/PT/CB 的数据问题修复，不要先扩大外部加载范围。

### 第二阶段：扩展显式域白名单

将外部模式从 PT-only 改为显式域白名单，例如：

```text
HARA_EXTERNAL_DOMAIN_PACKS=PT,CS,CB
```

或者采用单独配置对象：

```json
{
  "external_domains": ["PT", "CS", "CB"],
  "root": "..."
}
```

### 第三阶段：保持严格成对加载

每个域必须同时存在：

```text
<root>/<DOMAIN>/<DOMAIN>.json
<root>/<DOMAIN>/<DOMAIN>_subfunctions.json
```

任意一项缺失都不能回退到安装目录，避免出现：

```text
Pack 使用外部版本
S1 子功能表使用安装版本
```

### 第四阶段：加载前执行域内一致性检查

至少检查：

```text
domain 一致
case_count 一致
子功能 record_count 一致
source domain 不跨域
case IDs 不跨域
goal IDs 不跨域
analysis_unit IDs 不跨域
assessment 完整性
```

---

# 8. 推荐的正式准入规则

建议在案例库进入正式预填前增加以下准入规则。

## 8.1 可以 exact 锁定的案例

必须同时满足：

```text
function_profile.canonical_function_family 非空
failure_profile.canonical_mode 非空
failure_profile.anomaly_class 非空
hazard_profile.vehicle_hazard_class 非空
scenario_profile.semantic_fingerprint 非空
assessment.severity 非空
assessment.exposure 非空
assessment.controllability 非空
assessment.asil 非空
vehicle_safety_goal_id 非空
```

并且所有 exact 语义候选的 assessment 签名一致。

## 8.2 只能 similar/editable 的案例

满足以下任一情况时，只能作为可编辑候选：

```text
场景缺失
危害缺失
S/E/C/ASIL 任一缺失
整车安全目标缺失
异常类别未识别
存在多个 assessment 冲突候选
```

## 8.3 必须 none/needs_review 的案例

满足以下情况时，不应参与普通案例匹配：

```text
canonical_function_family 为空
scenario_text 为空且 hazard_profile 为空
anomaly_class = legacy_anomaly_
来源域与当前域不一致
案例 ID 或安全目标 ID 跨域
```

---

# 9. 推荐的修复顺序

## P0：暂不把 CS/PT/CB 全部当成正式无条件预填源

CS/PT 虽通过结构校验，但仍需保留字段级降级策略。

CB 不能直接进入正式运行时。

## P1：修复来源和跨域污染

优先修复：

```text
CB 的 CS 污染
CS 的来源哈希不一致
PT 的外部项目绝对路径
```

## P2：修复子功能权威表

确保：

```text
CS_subfunctions.json：22 条记录可审计
PT_subfunctions.json：90 条记录统一使用 PT 域标识
CB_subfunctions.json：补齐 8 条记录
```

## P3：修复未解析和不完整案例

重点处理：

```text
CS：81 条无安全目标（其中 16 条为 severity=0 非适用记录）
PT：1 条无功能族、18 条 legacy_anomaly_、20 条无危害/场景、20 条真正 assessment 不完整（另有 30 条 severity=0 非适用记录）、271 条无安全目标
CB：CB_CASE_000046 以及 11 条无安全目标；CB_CASE_000013 属于 severity=0 非适用记录
```

## P4：补齐功能族到子功能的语义连接

- CS：将安全机制明确连接到 exclude；
- PT：补齐增程功能的分析单元或明确转为 needs_review；
- CB：保留“电子驻车制动”为顶层功能族，但补充动态制动、静态驻车、外部请求等子功能语义。

## P5：最后再扩展外部域加载

在资产通过域内一致性和准入测试之后，再将外部路径机制从 PT 扩展到 CS、CB。

---

# 10. 最终判断

## CS

**可以作为候选外部预填资产，但不能忽略 81 条无安全目标案例和 16 条评估不完整案例。**

CS 的功能族 `steering_assist` 和主分析单元基本合理；16 条 severity=0 记录属于非适用分支，不是普通评估缺失；安全机制子功能需要通过 S1 权威表明确排除。

## PT

**主体功能族、分析单元和风险资产较完整，可以支撑大部分参考预填，但不能把 490 条案例全部视为可直接锁定的正式结果。**

PT 必须先处理：

- 1 条未解析功能族；
- 增程功能族缺少分析单元；
- 18 条 `legacy_anomaly_`；
- 20 条无危害/场景；
- 20 条真正 assessment 不完整，另有 30 条 severity=0 非适用记录；
- 271 条无整车安全目标；
- P/PT 域标识和来源路径问题。

## CB

**目前不能作为完整 Domain Pack 或正式外部案例库使用。**

CB 的顶层功能族“电子驻车制动”本身合理，但资产仍缺少完整运行时结构、独立子功能权威表为空，并混入了 CS 内容。

---

**本报告未修改 `E:\Git projects\hara-generator` 的业务代码或现有未提交变更。**
# 11. 新增问题：CB workspace 资产存在，但运行时没有读取

## 11.1 运行事实

本次 CB 运行输出显示：

```text
HARA_DOMAIN_PACKS_ROOT 仅对 PT 域生效
CB Domain Pack：从 installed_skill/.../references/domain_packs/CB/CB.json 读取
CB Domain Pack 状态：bootstrap，0 案例
CB 场景：从 installed_skill/.../references/scenarios/CB.json 读取，共 8 个场景集
S/E/C 参考：从 installed_skill/.../references/sec_reference_database.json 读取，共 149 条记录
workspace 中已审批入库的 CB 46 条案例：未被 hara-generator 运行时读取
```

这个结果与当前项目代码和 `SKILL.md` 的路径合同一致，不是 Agent 误报。

## 11.2 根因

当前 `scripts/utils/domain_packs.py` 只有 PT 进入严格外部模式：

```python
_PT_ALIASES = {"PT", "P", "POWERTRAIN"}
```

```python
def is_strict_external_pt(domain: str) -> bool:
    return (
        str(domain or "").strip().upper() in _PT_ALIASES
        and bool(os.environ.get("HARA_DOMAIN_PACKS_ROOT", "").strip())
    )
```

因此：

```text
PT + HARA_DOMAIN_PACKS_ROOT -> workspace
CB + HARA_DOMAIN_PACKS_ROOT -> installed
```

CB 正常流程实际加载：

```text
E:\Git projects\hara-generator\references\domain_packs\CB\CB.json
```

该文件是 bootstrap，案例数为 0。显式传入 `CaseLibrary(domain_pack_dir=...)` 可以做离线探针，但默认 HARA 流程不会自动读取下载目录或 Agent workspace 中的 CB 文件。

## 11.3 为什么仍然有 CB 场景和 SEC 数据

CB Pack 未切换到 workspace 后，场景加载器继续执行非 PT 的兼容路径：

1. 尝试加载安装目录 CB Pack；
2. 由于 bootstrap Pack 没有可用的单源场景合同，读取 `references/scenarios/CB.json`；
3. S4 没有被有效案例库控制时，调用 `lookup_sec_for_scenario()` 读取 `references/sec_reference_database.json`。

所以日志中的“8 个场景集”和“149 条 SEC 记录”只证明旧的安装包场景/全局参考回退路径生效，不能证明 workspace 46 条 CB 案例生效。

## 11.4 业务影响

这是 **P0 运行时数据源问题**，优先级高于 CB 案例字段清洗。只要路径没有切换，已审批的 46 条 CB 案例即使内容正确，也不会影响 HARA 输出：

- S4 不能使用 CB 案例的 exact 语义匹配；
- 不会使用案例的 `source_refs`、安全目标、safe state 和 FTTI；
- S/E/C 可能来自全局 SEC 数据库，而不是已审批 CB 案例；
- Agent 可能继续使用安装包 CB 的 8 个旧场景集；
- `case_pack_controlled` 不会因为 workspace 46 条案例存在而开启；
- 用户看到的运行结果与今天审批入库的数据版本不一致。

## 11.5 解决目标

启用 CB 外部资产后，必须成对加载：

```text
<root>/CB/CB.json
<root>/CB/CB_subfunctions.json
```

并满足：

```text
source = workspace
case_count = 46（修复资产后）
缺少外部文件时直接失败
不得回退 installed bootstrap
```

一旦 CB workspace Pack 有效，案例优先合同应优先使用案例里的场景、S/E/C、整车安全目标、safe state 和 FTTI；旧 CB 场景和全局 SEC 只能作为明确标记的兼容参考，不能静默覆盖案例结果。

## 11.6 推荐实现方案

建议保留 `HARA_DOMAIN_PACKS_ROOT`，增加显式外部域白名单：

```text
HARA_DOMAIN_PACKS_ROOT=<workspace>/domain_packs
HARA_EXTERNAL_DOMAIN_PACKS=PT,CS,CB
```

解析规则：当当前域在白名单中时，统一加载：

```text
<root>/<DOMAIN>/<DOMAIN>.json
<root>/<DOMAIN>/<DOMAIN>_subfunctions.json
```

未在白名单中的域继续使用安装包资产。进入白名单的域严格禁止回退。

不建议第一步为每个域增加独立根变量，因为多个根目录容易造成 Pack、S1 和案例库版本不一致。

## 11.7 必须同步检查的代码范围

不能只改 `domain_packs.py`，还要同步检查：

1. `scripts/utils/domain_packs.py`：外部域白名单、Pack/S1 成对解析、缺失禁止回退、缓存来源；
2. `scripts/utils/domain_subfunction_source.py`：CS/CB 外部子功能表；
3. `scripts/utils/case_library.py`：非 PT 外部案例库和来源切换；
4. `scripts/utils/hara_rules.py`：workspace Pack 启用后禁止静默回退旧 CB 场景；
5. `scripts/stages/stage_hara.py`：案例库有效时启用案例优先合同，避免旧 SEC 覆盖案例；
6. `scripts/utils/run_manifest.py`：记录实际路径、SHA-256、`domain_asset_source` 和启用域；
7. `SKILL.md`：把 PT-only 合同改成显式域白名单合同。

## 11.8 第一阶段验收标准

第一阶段只解决“运行时读到哪套资产”，不同时修复 CB 全部业务字段：

```text
1. HARA_DOMAIN_PACKS_ROOT 指向 workspace 根目录
2. HARA_EXTERNAL_DOMAIN_PACKS=CB
3. resolve_domain_pack_source("CB").source == "workspace"
4. pack_path == <root>/CB/CB.json
5. subfunction_path == <root>/CB/CB_subfunctions.json
6. 修复后的 CB case_count = 46
7. 缺少外部文件时抛出明确错误，不回退 installed bootstrap
8. run_manifest 记录 workspace 路径和 SHA-256
```

---

# 12. 所有问题的优先级与进度

## 12.1 进度口径

本次完成了审查和运行时路径定位，尚未修改业务代码或重新生成资产：

```text
审查进度：██████████ 100%
修复进度：░░░░░░░░░░ 0%
```

## 12.2 优先级排序

| 优先级 | 编号 | 问题 | 状态 | 依赖 |
|---|---|---|---|---|
| P0 | RT-01 | CB workspace 资产未被运行时读取，仍使用 installed bootstrap | 待处理 | 无 |
| P0 | RT-02 | Pack、S1、案例库必须使用同一外部来源 | 待处理 | RT-01 |
| P0 | RT-03 | 外部文件缺失时禁止静默回退 | 待处理 | RT-01 |
| P0 | CB-01 | CB.json 缺少完整运行时结构 | 待处理 | 可与 RT-01 并行审查 |
| P0 | CB-02 | CB_subfunctions.json 为空，S1 无法工作 | 待处理 | RT-01 |
| P1 | CB-03 | CB 混入 CS 来源、目标和风险矩阵 | 待处理 | CB-01 |
| P1 | CS-01 | CS 81 条案例无安全目标，其中 16 条是 severity=0 非适用记录 | 待处理 | 准入规则 |
| P1 | PT-01 | PT 271 条案例无安全目标，其中 30 条是 severity=0、20 条 assessment 真不完整 | 待处理 | 准入规则 |
| P1 | PT-02 | PT 20 条案例缺少危害/场景 | 待处理 | 准入规则 |
| P1 | PT-03 | PT 1 条案例无功能族 | 待处理 | 功能语义确认 |
| P1 | PT-04 | PT 18 条 `legacy_anomaly_` | 待处理 | 异常归一化 |
| P1 | CS/PT/CB-01 | 非 severity=0 的 assessment 不完整案例必须降级 editable | 待处理 | 准入规则 |
| P2 | CS-02 | CS 安全机制需要显式 exclude | 待处理 | S1 权威表 |
| P2 | PT-05 | pt_range_extender 有 matcher 但没有完整分析单元 | 待处理 | 业务范围确认 |
| P2 | CB-04 | CB 顶层功能合理但缺少子功能语义关联 | 待处理 | CB 子功能表 |
| P2 | PT-06 | PT 子功能记录使用 P 而不是 PT | 待处理 | 资产重生成 |
| P2 | PROV-01 | CS/PT/CB 来源路径和 SHA-256 不可复现或跨项目 | 待处理 | 资产重生成 |
| P3 | RT-04 | 缓存、运行清单和多域外部模式回归测试 | 待处理 | RT-01~03 |
| P3 | QA-01 | 增加域内一致性和 exact 准入自动校验 | 待处理 | 资产清洗后 |

## 12.3 修复看板

```text
P0 运行时路径与禁止回退      [░░░░░░░░░░] 0%
P0 CB 资产可加载性            [░░░░░░░░░░] 0%
P1 跨域污染和来源一致性       [░░░░░░░░░░] 0%
P1 案例字段完整性和准入规则    [░░░░░░░░░░] 0%
P2 功能族/子功能语义连接       [░░░░░░░░░░] 0%
P3 回归测试与运行清单          [░░░░░░░░░░] 0%

总体：                         [░░░░░░░░░░] 0%（0/6 个修复阶段完成）
```

---

# 13. 一次只解决一个问题的执行顺序

## RT-01 实施记录

本次已按确认范围实施 RT-01：仅扩展 Domain Pack、子功能权威表和内嵌案例库的外部来源；`scenarios`、`sec_reference_database.json`、Profile、Alias、白名单和 Excel 模板仍保持项目原有路径。

当前实现状态：

```text
代码修改：已完成（本地）
本地 Windows workspace 验收：待执行
Agent 最终路径：/home/gem/.aily/installed_skill/skills/hara-generator/1.29.0/references/domain_packs

实施进度：██████░░░░ 60%
``

下一步必须完成本地多域回归测试后，才能将 RT-01 标记为完成。

## 第 1 项：RT-01

先扩展外部域路径解析，使 CB 能从 workspace 读取。此项只处理：

```text
路径解析、外部域白名单、CB Pack/S1 成对加载、来源识别、缓存切换
```

暂不处理：

```text
CB 案例字段缺失、CB 功能族语义补充、CB 安全目标重新生成
```

没有完成 RT-01，后续修复的 CB 资产无法证明会被 HARA 使用。

## 第 2 项：CB-01 / CB-02

修复 CB Pack 结构和 `CB_subfunctions.json`，让 workspace CB 至少能通过结构、计数、域一致性和来源检查。

## 第 3 项：CB-03

清除 CB 中的 CS 目标、CS 映射和 CS 风险矩阵。

## 第 4 项：准入规则

把危害、场景、S/E/C/ASIL、整车安全目标不完整的案例降级为 editable/needs_review，同时保留 severity=0 的合法非适用合同。

## 第 5 项：专项语义问题

处理 CS 安全机制 exclude、PT 增程功能、PT `legacy_anomaly_`、CB 子功能语义关联。

## 第 6 项：回归测试和最终启用

完成运行清单、缓存、缺失资产失败和多域 workspace 模式的回归测试后，再正式启用 CS/PT/CB 外部资产。

---

# 14. 当前行动点

当前第一行动项是：

> **RT-01：扩展外部域路径解析，使 CB 能从 workspace 读取。**

建议下一步只做 RT-01 的代码设计和测试，不要同时修改 CB 的业务数据。RT-01 的目标验证结果是：

```text
source = workspace
pack_path = <workspace>/CB/CB.json
subfunction_path = <workspace>/CB/CB_subfunctions.json
case_count = 46（资产修复后）
```

验证通过后再进入 CB 资产修复。

---

**本次新增的 CB 运行时路径问题、优先级排序和修复进度看板已纳入本报告；RT-01 已按用户确认范围实施，后续资产清洗仍需单独确认。**
---

# 附录 A：数据统计口径更正

本报告早期草稿曾使用简单真假判断统计空值，可能把合法的数值 `0` 统计为缺失。根据项目当前 `CaseLibrary._assessment_complete_for_exact` 合同，以下统计口径为最终口径：

| 域 | severity=0 特殊记录 | 真正不满足 assessment exact 合同 | 备注 |
|---|---:|---:|---|
| CS | 16 | 0 | 16 条均为无整车层面危害分支 |
| PT | 30 | 20 | 20 条需 needs_review，30 条为 severity=0 非适用分支 |
| CB | 1 | 1 | CB_CASE_000013 为 severity=0；CB_CASE_000046 真正不完整 |

后续校验必须区分：

```text
0       = 可能是合法的 S0 / 非适用值
None    = 字段未提供
空字符串 = 文本未提供
```

不能使用 `if not value` 代替字段级缺失判断。

---
