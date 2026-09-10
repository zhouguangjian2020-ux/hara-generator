# DOC parsing 修复与兼容性回归报告

日期：2026-09-09

## 结论

- 修复章节编号重复、编号导致的子功能匹配降级、目的/兄弟功能描述串入等问题。
- 仅调整3个解析相关模块，新增1个测试文件；项目内的发布副本同步相同文件。
- Excel writer、S1～S5及其他原有Python模块均未修改：已与修改前SHA-256逐文件比对。
- 根目录及发布副本分别通过42项测试（原有21项 + 新增20项定向测试 + 1项覆盖全部11组的回归测试），无跳过。
- 全部11组真实DOCX、541条子功能完成解析；11次实际parse CLI执行成功。
- 11组S1规则结果、S2参考建议与修改前完全一致。
- 原有Excel writer对全部541条记录完成写入、XLSX内存保存及重新读取验证；没有覆盖原始Excel。

## 修改文件与边界

1. `scripts/utils/docx_parser.py`：显式章节编号只保留一次，通用节点和legacy章节统一使用同一label；功能描述使用专用选取函数。
2. `scripts/utils/document_context.py`：新增小型标题编号切分函数和功能描述选取函数；原有`build_node_summary_map()`、`node_summary()`及上下文分类行为保持不变。
3. `scripts/utils/chapter_matcher.py`：匹配时忽略明确的标题编号；legacy/deep匹配均使用功能描述；取消向父级或第一个兄弟章节借描述的回退；缺少描述时沿用待复核列表并标记原因。
4. `scripts/tests/test_document_parsing.py`：补充单元及可选真实文档回归测试。

未新增对外JSON schema，`chapter`和`description`仍为原字段。通用上下文的节点数、正文、source_index、evidence_ref均与基线一致，仅显式编号节点的label得到修正；通用摘要结果逐节点保持一致。

## 数据组回归结果

“未匹配”只统计status=unmatched；“待复核”统计low_confidence。原有未匹配不作为本次新增问题。

| 数据组 | 子功能数 | 关联章节变化数 | 描述变化数 | 未匹配（前→后） | 待复核（前→后） | S1/S2 | Writer回读 |
|---|---:|---:|---:|---:|---:|---|---|
| 数据组1 | 7 | 0 | 7 | 0 → 0 | 0 → 0 | 完全一致 | 通过 |
| 数据组2 | 6 | 0 | 4 | 0 → 0 | 1 → 1 | 完全一致 | 通过 |
| 数据组3 | 2 | 0 | 2 | 0 → 0 | 0 → 0 | 完全一致 | 通过 |
| 数据组4 | 45 | 0 | 29 | 12 → 12 | 0 → 0 | 完全一致 | 通过 |
| 数据组5 | 87 | 0 | 81 | 6 → 6 | 0 → 1 | 完全一致 | 通过 |
| 数据组6 | 21 | 0 | 20 | 0 → 0 | 1 → 1 | 完全一致 | 通过 |
| 数据组7 | 8 | 0 | 8 | 0 → 0 | 0 → 0 | 完全一致 | 通过 |
| 数据组8 | 15 | 15 | 15 | 0 → 0 | 0 → 0 | 完全一致 | 通过 |
| 数据组9 | 29 | 0 | 24 | 5 → 5 | 0 → 0 | 完全一致 | 通过 |
| 数据组10 | 196 | 0 | 18 | 124 → 124 | 4 → 4 | 完全一致 | 通过 |
| 数据组11 | 125 | 0 | 104 | 12 → 12 | 3 → 4 | 完全一致 | 通过 |

所有数据组的功能数量、ID、名称、场景和功能表来源对应关系不变。除数据组8的15条预期章节修正外，其余10组的关联章节结果完全不变；没有新增真正的章节未匹配。

312条描述发生变化，分为：去掉目的/接口/条件补齐；父级功能不再任意只截取两块子功能描述；无单独“功能描述”标题的文档在目标节点范围内保留正文回退。合法原文分号不删除，父级功能汇总不跨出其子树。

### CH STEERING 核心验收

- 15条子功能全部精确匹配；关联章节均无重复编号。
- 转向模式控制：`3.1.11 转向模式控制`。
- 基础助力：`3.1.2.1 基础助力`。描述严格为“控制单元会根据实时车速和方向盘手力矩提供对应转向助力。”；不再包含转角监测描述或目的。
- 摩擦补偿：描述严格为“根据转向电机旋转方向、方向盘手力矩和车速计算系统摩擦力矩。”。

## 两处源文档问题（显式保留待复核）

### 数据组5：制动能量回收（L2）

关联章节为3.4.10。DOCX解析到的正文把“功能描述”混在“目的”正文末尾，没有形成独立描述标题，后续IBS需求/动力域需求也位于目的节点之下。旧逻辑将目的和需求片段拼为描述。现在不把它们自动当成独立功能描述，description留空，并记录`matched_section_description_missing`。

### 数据组11：方向盘电动调节

关联章节为3.64方向盘调节（L3）；下属“按键调节方向盘（L3）”包含目的及其下的车身域需求，没有独立功能描述标题。旧逻辑将外部接口的“无”和目的拼接。现在description留空并记录同一复核原因。

这两项没有丢失功能记录或章节，只是不再伪装成已取得可靠描述。建议在源DOCX补齐明确的功能描述标题/正文后重新解析，或沿用现有Agent文档复核流程。未修改原有exact案例路由的非阻塞规则，是否阻断后续阶段仍由原有规则决定。Writer原有“description为空则回退name”的行为保持不变。

## 保留的既有边界

- 未重写完整Word自动编号解析算法；未显式编号的标题保持既有层级计数行为。原文显式编号冲突时保留原文，不替用户修改DOCX内容。
- 上下文截取上限维持现有设置：单节点最多3段/600字符，功能描述最多1200字符；本次不扩展全文解析模型。
- 未重新生成完整S3～S5业务分析；验证包括既有下游测试、11组S1/S2结果对照及原Writer兼容性。
- 未重打包`hara-generator.zip`，未改动案例库/模板/其他现有业务修改。

## 复现命令

在项目根目录执行（使用已安装python-docx和openpyxl的解释器）：

```powershell
Set-Location -LiteralPath 'E:\Git projects\hara-generator'
$env:HARA_DOCX_TEST_ROOT='E:\Git projects\hara-skill 补充数据\数据组'
$env:HARA_PARSE_BASELINE='C:\Users\zhouguangjian\Documents\ChatGPT\HARA\outputs\doc-parsing-regression-20260909\baseline.json'
python -B -X utf8 -m unittest discover -s scripts/tests -v
```

`HARA_DOCX_TEST_ROOT`未设置时，真实文档测试明确跳过；设置后才覆盖11组。`HARA_PARSE_BASELINE`用于额外执行修复前后字段、章节、S1/S2比较。真实DOCX不打包进项目。

## 审计文件

目录：`C:\Users\zhouguangjian\Documents\ChatGPT\HARA\outputs\doc-parsing-regression-20260909`

- `baseline.json` / `after.json`：修复前后解析快照。
- `diffs.json` / `summary.json`：逐功能差异及数据组汇总。
- `decision-compatibility.json`：S1/S2逐组比较。
- `tests-final.log` / `tests-release-copy.log`：两份代码的42项测试记录。
- `cli-results.json` / `cli/数据组*/parse.log`：11次真实CLI测试。
- `source-hashes-before.json` / `changed-python-files.json`：修改范围审计。
