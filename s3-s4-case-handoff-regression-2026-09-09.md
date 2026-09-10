# S3→S4 来源案例交接修复与验收报告

日期：2026-09-09
项目：`E:\Git projects\hara-generator`
验收材料：`C:\Users\zhouguangjian\Documents\ChatGPT\HARA\outputs\s3-s4-handoff-regression-20260909`
外部案例库：`C:\Users\zhouguangjian\Documents\ChatGPT\HARA\domain_packs`

## 1. 本轮结论

受控 S3 案例库输出现在携带 `failure_profile.canonical_mode` 与 `failure_profile.anomaly_class`。S4 prepare 保存已验证的同一 profile，S4 validate 复用它并与来源案例、当前 S3 追溯信息交叉校验。

本轮未修改 Excel writer、DOCX parsing、case_library.py 的文本归一化规则、案例库内容、Python 导入机制或系统环境变量；未使用 PYTHONPATH/sitecustomize 补丁覆盖。

仅修改两个现有业务模块，并新增一个测试文件；三份文件同步至项目内的 `hara-generator` 发布副本。已有 ZIP 未重打包，远端安装版本未部署。

## 2. 修复前的真实复现

使用数据组8的 steering intermediate 和当前外部 CS 案例库：

1. 标准 S2 generate/compose、S3 generate/compose 通过。
2. 原始 S4 prepare 失败：`source case_ids 与当前 S3 功能/失效/异常/危害语义不一致`。
3. 仅在实验副本的 S3 手工补 `anomaly_class` 后，prepare 生成5个危害组、95条事件。
4. 对此骨架执行来源锁定复核，5个危害组全部再次报语义不一致。

证据：`baseline-reproduction.json`；`group8/s3.json`；`group8/s3_manual_class_before.json`；`group8/s4_manual_class_before.json`。

根因是结构化类别在 S3 输出及 S4 骨架中丢失，S4 回退到文本推断。现有中文转向描述均未命中归一化规则，被过滤为 `legacy_anomaly_`，不等于来源案例的标准类别。

## 3. 修改范围

### scripts/utils/domain_pack_generation.py

- 在 source failure 分组内确认标准异常类别唯一且非空，避免把语义不同的案例静默合并。
- 生成 S3 时复制来源 `failure_profile`；不把显示用 alias 当作标准类别。
- S3 校验同时锁定 profile、case_ids、source_refs、source_failure_id 和案例库 source。
- 缺少新字段的旧受控 S3 给出重新 generate/compose 的明确提示，不静默接受或重新猜测。

### scripts/stages/stage_hara.py

- prepare/validate 共用 `_build_failure_profile`，结构化字段优先；无结构化字段的旧路径保留原文本回退。
- 拒绝内外失效模式冲突、两处异常类别冲突、非法 profile 类型。
- 只对来源案例锁定组新增保存 `failure_profile`，不改其他骨架分支。
- validate 重新解引用可信来源案例并比较事件；profile 还必须与提供的本轮 S3 一致。
- 不放宽 trusted_reference、S/E/C、ASIL、安全目标、安全状态、FTTI 等原有质量闸门。

### scripts/tests/test_source_case_handoff.py

新增17项测试，涵盖：

- CS完整 S2/S3 generate/compose/validate → S4 prepare/validate；
- 将 `canonical_anomaly_class` 临时设为“调用即失败”，确认受控 CS/CB/PT 路径不再调用文本推断；
- CB标准英文类别与中文 alias 的区分、多危害分组和“过多/无危害”跳过行为；
- PT完整案例流程、旧版无 profile 的可识别 PT 锁定结果、失效模式别名；
- 缺失/篡改 S3 profile、案例ID、来源引用、来源失效ID、source 与受控草稿时拒绝；
- 缺失/篡改 S4 profile、篡改锁定事件、降低案例证据等级时拒绝；
- 分组类别不一致/缺失时拒绝；生成结果不反向修改案例库对象。

## 4. 验收结果

### 自动化测试

| 范围 | 结果 |
|---|---|
| 主项目完整测试，启用11组真实DOCX测试 | 59项全部通过，无跳过 |
| 内嵌发布副本完整测试 | 59项全部通过，无跳过 |
| 使用指定外部 Domain Pack 根目录的专项测试 | 17项全部通过 |

日志：`tests-final.log`、`tests-release.log`、`tests-external-handoff.log`。

### 正常 CLI 全阶段验收

每个阶段启动独立 Python 进程；命令直接执行对应完整目录的 `scripts/run_hara.py`，清除测试进程中的 PYTHONPATH/PYTHONHOME 干扰，设置同一外部 Domain Pack 根目录。没有混合补丁包。

以下结果在主项目及发布副本中相同：

| 输入范围 | S3/S4结果 | 最终校验 |
|---|---|---|
| 数据组8 CS 转向 | 5个危害组，95条来源案例事件 | 0错误，30条原有内容/分布警告 |
| 数据组1 CS 转向 | 5个危害组，95条来源案例事件 | 0错误，30条原有内容/分布警告 |
| 数据组5 PT，仅截取“档位控制及显示”相关项 | 105条来源案例事件 | 0错误 |
| 数据组2 CB EPB | 9个危害组，1个无危害跳过，45条事件 | 19个案例数据质量错误，按预期阻止正式发布 |
| 数据组7 CB EPB | 9个危害组，1个无危害跳过，45条事件 | 同上 |

PT截取范围已写入测试 intermediate 的 `regression_scope`，不是整份PT文档的最终HARA结论。S1 pending 决策仅为回归测试输入，不表示代替工程审批。

各阶段命令、退出码及详细日志：`cli-main/summary.json`、`cli-release/summary.json`。

### 兼容性和改动边界

- 从完整的修复前代码副本与修复后项目分别运行11组 DOCX parse，共22次正常CLI解析。
- 541个子功能的 intermediate 全字段一致；只归一化不同运行目录及输出目录的绝对路径，没有删除业务字段。
- 子功能数依次为：7、6、2、45、87、21、8、15、29、196、125。
- CS修复前后 S3 entries 完全一致，唯一新增字段为 failure_profile。
- CS修复后 S4 hazards 与修复前“手工补类别后生成的骨架”完全一致，唯一新增字段为 failure_profile；95条事件、描述、S/E/C及工程字段未被改写。
- SHA-256核对：除两个业务模块的主/发布副本之外，其他既有 Python 文件均未改动。新增项只有两份测试文件。
- 两个正常运行目录的模块导入位置均已记录和核对。

证据：`docx-comparison.json`、`cs-output-compatibility.json`、`changed-python-files.json`、`runtime-import-paths.json`、`implementation.diff`。

## 5. CB案例库独立问题：未修改、未绕过

S3/S4的功能、失效、异常、整车危害语义交接已通过，正式校验仍发现以下来源数据问题：

- 来源案例表第9、26、46行：库中写QM，但项目现有S/E/C规则重算为A；对应安全目标、安全状态、FTTI为空。引发3项ASIL来源不一致及相关必填/锁定错误。
- 第24行：FTTI为 `501ms(TBD)`，所属组预填值为 `500ms(TBD)`，发生锁定值冲突。
- 总计19条正式校验错误。未生成 `s4_final.json`，保留 `.validation_failed` 标记，防止下游错误使用。

这是源记录与现有规则/组约束的不一致，并非本次新增字段造成。具体记录和原始 assessment 见 `cb-existing-data-issues.json`。本轮未将QM改为A，也未修改FTTI或补填工程结论；需单独复核权威案例源。

## 6. 历史测试基线说明

额外启用上一轮的 `HARA_PARSE_BASELINE` 历史快照时，修改前42项测试和修改后58项测试均在同一真实文档测试的组2、组7子测试中失败，显示的是 pending 子功能索引顺序差异。未修改或放宽这个既有测试断言。

正式59项测试启用真实DOCX输入，不启用该旧快照；同时另做了本轮完整修复前/后的11组直接CLI输出比较，全部一致。相关历史结果保留在 `tests-before.log` 与 `tests-with-historical-baseline.log`，避免把基线问题误报为本次引入或隐藏。

## 7. 部署与重跑要求

- 将本次完整代码目录统一部署，所有阶段使用同一个入口和解释器。不要仅复制 case_library.py，也不需要 PYTHONPATH/sitecustomize 覆盖。
- 远端 Domain Pack 配置保持用户指定值：`/home/gem/.aily/workspace/references/domain_packs`。
- 确认 intermediate/S1/S2与当前案例库一致后，重新执行 S3 generate → compose → validate → S4 prepare → validate。
- 旧受控 S3若缺少 failure_profile，需要重新生成；本次没有自动迁移或改写历史运行文件。
- 本轮未重打包 hara-generator.zip，未部署到远端只读 installed_skill，也未覆盖用户原Excel。
