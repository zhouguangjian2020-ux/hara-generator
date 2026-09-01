# S/E/C 评定参考（ISO 26262）

> 本文件供 HAZOP → HARA 转换流程中的 Agent 使用。
> 内容来源：ISO/WD 26262-3, BL9, Annex B（Hazard Analysis and Risk Assessment — Examples）。
> 用途：为严重度（S）、暴露概率（E）、可控性（C）三个因子的等级评定提供**描述性定义、量化阈值与示例场景**，辅助 Agent 在 HARA 分析中对 S/E/C 做出合理判定。
> 注意：本文件中的示例为信息性参考（informative examples），适用于乘用车（passenger cars）及卡车客车（T&B, Trucks and Buses），但需逐案判断（case by case）。

---

## 1. 严重度等级（Severity, S）

### 1.1 等级定义与 AIS 映射（Table B.1）

| 等级 | 描述（中/英） | 单一伤害参考（AIS 量表） |
|------|--------------|------------------------|
| **S0** | 无伤害（No injuries） | AIS 0；或 AIS 1-6 概率 < 10%；或无法归类为安全相关的损坏 |
| **S1** | 轻度及中度伤害（Light and moderate injuries） | AIS 1-6 概率 > 10%（且不属于 S2 或 S3） |
| **S2** | 严重及危及生命的伤害，存活可能（Severe and life-threatening injuries, survival probable） | AIS 3-6 概率 > 10%（且不属于 S3） |
| **S3** | 危及生命的伤害，存活不确定；致命伤害（Life-threatening injuries, survival uncertain; fatal injuries） | AIS 5-6 概率 > 10% |

> **AIS** = Abbreviated Injury Scale（简明损伤定级）。也可使用 MAIS、ISS、NISS 等其他伤害分类量表，具体取决于分析时的医学研究水平。

### 1.2 严重度示例（Table B.1）

| 场景类别 | S0 | S1 | S2 | S3 |
|---------|----|----|----|-----|
| 侧面碰撞（窄静止物，如树，撞击乘员舱） | — | 极低速（very low speed） | 低速（low speed） | 中速（medium speed） |
| 后/正面碰撞（与另一乘用车） | — | 极低速（very low speed） | 低速（low speed） | — |
| 后/正面碰撞（与另一车辆） | — | — | — | 中速（medium speed） |
| 正面碰撞（追尾其他车辆、半挂车等） | — | 乘员舱无变形（without passenger compartment deformation） | — | 乘员舱变形（with passenger compartment deformation） |
| 行人/自行车事故 | — | — | 低速（low speed） | — |
| 与路边设施刮碰（Bumps with roadside infrastructure） | ✓ | — | — | — |
| 推倒路边立柱/围栏等（Pushing over roadside post, fence, etc.） | ✓ | — | — | — |
| 轻微刮擦损伤（Light grazing damage） | ✓ | — | — | — |
| 进出停车位时的损坏（Damage entering/exiting parking space） | ✓ | — | — | — |
| 驶离道路但无碰撞或翻滚（Leaving the road without collision or rollover） | ✓ | — | — | — |

### 1.3 AIS 伤害等级详细说明

| AIS 等级 | 伤害描述 |
|----------|---------|
| **AIS 0** | 无伤害（no injuries） |
| **AIS 1** | 轻度伤害：如表皮伤口、肌肉疼痛、挥鞭伤等（light injuries such as skin-deep wounds, muscle pains, whiplash etc.） |
| **AIS 2** | 中度伤害：如深部肌肉伤口、昏迷不超过 15 分钟的脑震荡、无并发症的长骨骨折、无并发症的肋骨骨折等（moderate injuries such as deep flesh wounds, concussion with up to 15 minutes of unconsciousness, uncomplicated long bone fractures, uncomplicated rib fractures etc.） |
| **AIS 3** | 严重但不危及生命：如无脑损伤的颅骨骨折、第四颈椎以下脊柱脱位且无脊髓损伤、多根肋骨骨折但无反常呼吸等（severe but not life-threatening injuries such as skull fractures without brain injury, spinal dislocations below the fourth cervical vertebra without damage to the spinal cord, more than one fractured rib without paradoxical breathing etc.） |
| **AIS 4** | 严重伤害（危及生命，存活可能）：如伴或不伴颅骨骨折的昏迷不超过 12 小时、反常呼吸等（severe injuries (life-threatening, survival probable) such as concussion with or without skull fractures with up to 12 hours of unconsciousness, paradoxical breathing） |
| **AIS 5** | 危重伤害（危及生命，存活不确定）：如第四颈椎以下脊柱骨折伴脊髓损伤、肠破裂、心脏破裂、昏迷超过 12 小时伴颅内出血等（critical injuries (life-threatening, survival uncertain) such as spinal fractures below the fourth cervical vertebra with damage to the spinal cord, intestinal tears, cardiac tears, more than 12 hours of unconsciousness including intracranial bleeding） |
| **AIS 6** | 极危重或致命伤害：如第三颈椎以上颈椎骨折伴脊髓损伤、体腔（胸腔和腹腔）极危重开放性伤口等（extremely critical or fatal injuries such as fractures of the cervical vertebrae above the third cervical vertebra with damage to the spinal cord, extremely critical open wounds of body cavities (thoracic and abdominal cavities) etc.） |

### 1.4 注意事项（Remark）

由于事故的复杂性和事故场景的多种可能变化，上述示例仅为事故后果的粗略估计，代表基于以往事故分析的期望值，不能从这些个别描述中得出普遍适用的结论。

---

## 2. 暴露概率等级（Exposure, E）

暴露概率有两个评定维度：**持续时间（duration）** 和 **发生频率（frequency）**。

### 2.1 按持续时间评定（Table B.2）

| 等级 | 描述 | 持续时间（占平均运行时间百分比） |
|------|------|-------------------------------|
| **E1** | 极低概率（Very low probability） | 未规定（Not specified） |
| **E2** | 低概率（Low probability） | < 1% 的平均运行时间 |
| **E3** | 中等概率（Medium probability） | 1% ~ 10% 的平均运行时间 |
| **E4** | 高概率（High probability） | > 10% 的平均运行时间 |

#### 按持续时间的示例（Table B.2）

| 示例类别 | E1 | E2 | E3 | E4 |
|---------|----|----|----|-----|
| 道路布局（road layout） | — | 乡村道路交叉口（Country road intersection）；高速公路出口匝道（Highway exit ramp） | 单行道/城市街道（One-way street / city street） | 高速公路（Highway）；乡村道路（Country road） |
| 路面状况（road surface） | — | 路面积雪结冰（Snow and ice on road）；路面湿滑落叶（Slippery leaves on road） | 湿滑路面（Wet road） | — |
| 车辆静止状态（vehicle stationary state） | 车辆跨接启动时（Vehicle during jump start）；在维修车间内（In repair garage） | 挂接拖车（Trailer attached）；安装车顶行李架（Roof rack attached）；车辆加油中（Vehicle being refuelled） | 车辆在坡道上（Vehicle on a hill / hill hold） | — |
| 驾驶操作（manoeuvre） | 熄火下坡行驶（山道）（Driving downhill with engine off / mountain pass） | 倒车（Driving in reverse）；超车（Overtaking）；停车（挂接拖车时）（Parking with trailer attached） | 拥堵交通（走走停停）（Heavy traffic / stop and go） | 加速（Accelerating）；减速（Decelerating）；在交通信号灯前停车（城市街道）（Stopping at traffic light / city street）；变道（高速公路）（Lane change / highway） |

### 2.2 按发生频率评定（Table B.3）

| 等级 | 描述 | 发生频率 |
|------|------|---------|
| **E1** | 极低概率（Very low probability） | 对绝大多数驾驶员而言少于每年一次（Occurs less often than once a year for the great majority of drivers） |
| **E2** | 低概率（Low probability） | 对绝大多数驾驶员而言每年发生数次（Occurs a few times a year for the great majority of drivers） |
| **E3** | 中等概率（Medium probability） | 对普通驾驶员而言每月一次或更频繁（Occurs once a month or more often for an average driver） |
| **E4** | 高概率（High probability） | 几乎每次驾驶都会发生（Occurs during almost every drive on average） |

#### 按频率的示例（Table B.3）

| 示例类别 | E1 | E2 | E3 | E4 |
|---------|----|----|----|-----|
| 道路布局（road layout） | — | 无防护陡坡的山道（Mountain pass with unsecured steep slope） | — | — |
| 路面状况（road surface） | — | 路面积雪结冰（Snow and ice on road） | 湿滑路面（Wet road） | — |
| 车辆静止状态（vehicle stationary state） | 停车后需重新启动发动机（如铁路道口）（Stopped, requiring engine restart at railway crossing）；车辆被牵引（Vehicle being towed） | 安装车顶行李架（Roof rack attached） | 车辆加油中（Vehicle being refuelled）；车辆在坡道上（Vehicle on a hill / hill hold） | — |
| 驾驶操作（manoeuvre） | — | 紧急避让操作，偏离预期路径（Evasive manoeuvre, deviating from desired path） | 超车（Overtaking） | 换挡（Shifting transmission gears）；转弯（转向）（Executing a turn / steering）；使用转向灯（Using indicators）；倒车（Driving in reverse） |

---

## 3. 可控性等级（Controllability, C）

### 3.1 等级定义与量化阈值（Table B.6）

| 等级 | 描述 | 能够避免伤害的驾驶员/交通参与者比例 |
|------|------|----------------------------------|
| **C0** | 通常可控（Controllable in general） | 通常可控（Controllable in general） |
| **C1** | 简单可控（Simply controllable） | > 99% 的普通驾驶员或其他交通参与者能够避免伤害 |
| **C2** | 正常可控（Normally controllable） | 90% ~ 99% 的普通驾驶员或其他交通参与者能够避免伤害 |
| **C3** | 难以控制或不可控（Difficult to control or uncontrollable） | < 90% 的普通驾驶员或其他交通参与者能够避免伤害 |

> **注意**：ASIL 判定表（ISO 26262-3 Table 4）仅使用 C1/C2/C3；C0 表示通常可控，不参与 ASIL 升级（等价于 QM 方向）。

### 3.2 可控性示例（Table B.6）

| 危险事件场景 | C0 | C1 | C2 | C3 |
|------------|----|----|----|-----|
| 分散注意力的情况（如收音机音量意外增大、警告信息——燃油不足）（Distracting situations, e.g. unexpected radio volume increase or warning message - fuel low） | 保持预期行驶路径（Maintain intended driving path） | — | — | — |
| 不影响车辆安全运行的驾驶员辅助系统不可用（Unavailability of a driver assisting system that does not affect safe operation） | 保持预期行驶路径（Maintain intended driving path） | — | — | — |
| 驾驶中车窗意外关闭（Unintended closing of window while driving） | — | 将手臂从窗口移开（Remove arm from window） | — | — |
| 从静止加速时转向柱锁止（Blocked steering column when accelerating from standstill） | — | 制动以减速/停车（Brake to slow/stop vehicle） | — | — |
| 紧急制动时 ABS 失效（Failure of ABS during emergency braking） | — | — | 保持预期行驶路径（Maintain intended driving path） | — |
| 高侧向加速度时动力失效（Propulsion failure at high lateral acceleration） | — | — | 保持预期行驶路径（Maintain intended driving path） | — |
| 公交车行驶中车门意外打开、有乘客站在门口（Inadvertent opening bus door while driving with passenger standing in doorway） | — | — | 乘客抓住扶手以避免摔出公交车（Passenger grabs hand rail to avoid falling out of bus） | — |
| 制动失效（Failure of brakes） | — | — | — | 转向避开行驶路径上的物体（Steer away from objects in driving path） |

### 3.3 注释（NOTES）

- **NOTE 1（C2 验证方法）**：对于 C2，按照 RESPONSE 3（见参考文献 [4]）的可行测试场景可作为充分依据："实际测试经验表明，每个场景 20 个有效数据集可提供基本的有效性指示。"如果 20 个数据集均满足测试通过标准，则可证明 85% 的可控性水平（置信度 95%，这在人因测试中被普遍接受）。这是 C2 估计的合理依据。
- **NOTE 2（C1 验证方法）**：对于 C1，通过测试证明 99% 的驾驶员在特定交通场景中"通过"测试可能不可行，因为需要大量测试对象作为依据。可基于专家判断（expert judgement）做出决定。
- **NOTE 3（C3 验证方法）**：由于 C3 假定为不可控，因此不需要为此类分类提供合理依据的证据。
- **NOTE 4（适用范围）**：Table B.6 中的信息性示例适用于乘用车和卡车客车（T&B），但需逐案判断。

---

## 4. Agent 评定指引

### 4.1 S 评定流程

1. 分析危害事件可能导致的**最严重合理可预见伤害**。
2. 参照 AIS 量表（第 1.3 节）确定伤害等级。
3. 依据第 1.1 节的概率阈值（>10%）映射到 S0/S1/S2/S3：
   - 无伤害或安全无关损坏 → S0
   - AIS 1-6 概率 > 10%（未达 S2/S3） → S1
   - AIS 3-6 概率 > 10%（未达 S3） → S2
   - AIS 5-6 概率 > 10% → S3
4. 可参照第 1.2 节示例场景辅助判断（碰撞速度、乘员舱变形、行人事故等）。

### 4.2 E 评定流程

1. 根据场景特征选择评定维度：
   - **持续时间维度**：该运行状态占平均运行时间的比例（第 2.1 节）。
   - **频率维度**：该情况发生的频繁程度（第 2.2 节）。
2. 两个维度均可参考，取**更保守（更高等级）**的评定结果。
3. 参照对应示例表（道路布局、路面状况、车辆状态、驾驶操作）进行类比。
4. 阈值速查：
   - E1：极少发生 / 未规定时长
   - E2：< 1% 运行时间，或每年数次
   - E3：1%~10% 运行时间，或每月至少一次
   - E4：> 10% 运行时间，或几乎每次驾驶

### 4.3 C 评定流程

1. 评估驾驶员或其他交通参与者**通过合理反应避免伤害**的可能性。
2. 依据第 3.1 节的量化阈值判定：
   - C0：通常可控（不参与 ASIL 升级）
   - C1：> 99% 可避免
   - C2：90%~99% 可避免
   - C3：< 90% 可避免
3. 参照第 3.2 节示例场景（ABS 失效、制动失效、转向锁止等）类比。
4. C2 可通过测试数据支撑（20 个有效数据集，85% 可控性，95% 置信度）；C1 可基于专家判断；C3 无需证据。

### 4.4 综合注意事项

- 以上示例均为**信息性参考**，非穷尽列表，实际评定需结合具体场景逐案分析。
- S/E/C 评定应基于**合理可预见的误用和故障场景**，而非仅考虑最可能情况。
- 评定结果需在 HARA 表中记录**判定依据**（引用的示例类别或推理过程），以便追溯。
- S/E/C 确定后，使用 ASIL 判定表（见 `asil-determination-rules.md`）查表得出 ASIL 等级。
