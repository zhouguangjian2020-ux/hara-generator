# ASIL 判定规则（ISO 26262-3, Table 4）

> 本文件供 HAZOP → HARA 转换流程中的 Agent 使用。
> 输入：严重度等级（S）、暴露概率等级（E）、可控性等级（C）。
> 输出：ASIL 等级（S=0 时为空；S>0 时为 QM / A / B / C / D）。
> 数据来源：ISO 26262-3:2018 Table 4 — ASIL determination。

---

## 1. 三个输入因子

| 因子 | 含义 | 取值 |
|------|------|------|
| **S**（Severity，严重度） | 潜在伤害的严重程度 | S1、S2、S3 |
| **E**（Exposure，暴露概率） | 人员处于危险场景的概率 | E1、E2、E3、E4 |
| **C**（Controllability，可控性） | 驾驶员或其他人员避免伤害的可控性 | C1、C2、C3 |

### 1.1 严重度等级（S）

| 等级 | 说明 |
|------|------|
| S1 | 轻度伤害（light and moderate injuries） |
| S2 | 严重伤害（severe and life-threatening injuries, survival probable） |
| S3 | 致命伤害（life-threatening injuries, survival uncertain, or fatal injuries） |

### 1.2 暴露概率等级（E）

| 等级 | 说明 |
|------|------|
| E1 | 极低概率（very low probability） |
| E2 | 低概率（low probability） |
| E3 | 中等概率（medium probability） |
| E4 | 高概率（high probability） |

### 1.3 可控性等级（C）

| 等级 | 说明 |
|------|------|
| C1 | 简单可控（simply controllable） |
| C2 | 正常可控（normally controllable） |
| C3 | 难以控制或不可控（difficult to control or uncontrollable） |

---

## 2. ASIL 输出等级（由低到高）

> **S=0 特殊规则（项目统一约定）**：当 S=0 时，不评定 E/C；`exposure`、`exposure_reason`、`controllability`、`controllability_reason` 和 `asil` 全部留空，且不生成安全目标。S=0 事件仍必须保留在 HARA 中。

| 等级 | 含义 |
|------|------|
| **QM** | Quality Management，无需功能安全措施，按常规质量管理即可 |
| **A** | ASIL A，最低安全完整性等级 |
| **B** | ASIL B |
| **C** | ASIL C |
| **D** | ASIL D，最高安全完整性等级 |

---

## 3. ASIL 判定查找表

行索引 = (S, E)，列索引 = C，单元格值 = ASIL。

| Severity | Exposure | C1 | C2 | C3 |
|----------|----------|----|----|-----|
| S1 | E1 | QM | QM | QM |
| S1 | E2 | QM | QM | QM |
| S1 | E3 | QM | QM | A  |
| S1 | E4 | QM | A  | B  |
| S2 | E1 | QM | QM | QM |
| S2 | E2 | QM | QM | A  |
| S2 | E3 | QM | A  | B  |
| S2 | E4 | A  | B  | C  |
| S3 | E1 | QM | QM | A† |
| S3 | E2 | QM | A  | B  |
| S3 | E3 | A  | B  | C  |
| S3 | E4 | B  | C  | D  |

> † **脚注（特殊情况）**：S3 / E1 / C3 组合的判定值为 **A**，但 ISO 26262-3 第 6.4.3.11 条对该格有附加说明。Agent 在遇到此组合时，应将 ASIL 判定为 A，**同时标记 `footnote_clause: "6.4.3.11"` 并提示人工复核**，确认是否需要依据该条款调整。

---

## 4. 机器可读判定映射

以下 JSON 结构可直接被代码/Agent 加载用于查表。键格式为 `"S{E}-E{E}-C{C}"`，值为 ASIL 字符串。

```json
{
  "S1-E1-C1": "QM", "S1-E1-C2": "QM", "S1-E1-C3": "QM",
  "S1-E2-C1": "QM", "S1-E2-C2": "QM", "S1-E2-C3": "QM",
  "S1-E3-C1": "QM", "S1-E3-C2": "QM", "S1-E3-C3": "A",
  "S1-E4-C1": "QM", "S1-E4-C2": "A",  "S1-E4-C3": "B",

  "S2-E1-C1": "QM", "S2-E1-C2": "QM", "S2-E1-C3": "QM",
  "S2-E2-C1": "QM", "S2-E2-C2": "QM", "S2-E2-C3": "A",
  "S2-E3-C1": "QM", "S2-E3-C2": "A",  "S2-E3-C3": "B",
  "S2-E4-C1": "A",  "S2-E4-C2": "B",  "S2-E4-C3": "C",

  "S3-E1-C1": "QM", "S3-E1-C2": "QM", "S3-E1-C3": "A",
  "S3-E2-C1": "QM", "S3-E2-C2": "A",  "S3-E2-C3": "B",
  "S3-E3-C1": "A",  "S3-E3-C2": "B",  "S3-E3-C3": "C",
  "S3-E4-C1": "B",  "S3-E4-C2": "C",  "S3-E4-C3": "D"
}
```

特殊格标记：

```json
{
  "S3-E1-C3": {
    "asil": "A",
    "footnote": "See ISO 26262-3 clause 6.4.3.11",
    "requires_human_review": true
  }
}
```

---

## 5. 判定规则（Agent 执行逻辑）

1. **输入校验**：S 必须 ∈ {S1, S2, S3}；E 必须 ∈ {E1, E2, E3, E4}；C 必须 ∈ {C1, C2, C3}。任一输入非法或缺失，不得猜测，应标记为 `INDETERMINATE` 并提示补充。
2. **查表**：以 `(S, E, C)` 三元组为键，在第 4 节映射中取值。
3. **脚注处理**：若命中 `S3-E1-C3`，输出 ASIL = A 的同时附加脚注标记与人工复核标志。
4. **输出**：S=0 返回空值；S>0 返回 ASIL 等级（QM / A / B / C / D），不得输出表中不存在的值。
5. **单调性**：在其他因子不变时，S、E、C 各自等级升高，ASIL 不降低；可用于自检查表结果是否合理。

---

## 6. 快速示例

| 输入 (S, E, C) | 输出 ASIL | 备注 |
|----------------|-----------|------|
| S1, E1, C1 | QM | |
| S2, E4, C3 | C  | |
| S3, E4, C3 | D  | 最高等级 |
| S3, E1, C3 | A  | 需人工复核（脚注 6.4.3.11） |
| S1, E2, C2 | QM | |
