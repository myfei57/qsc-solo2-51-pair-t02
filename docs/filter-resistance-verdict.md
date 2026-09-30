# 滤床自身阻力判定规则书

> 目标：不再只看单点压差。用**不同流量下的压差样本**折算滤床自身阻力曲线，
> 与标定基准（干净新床曲线）比较，给出「该洗 / 该换 / 判不了（弃权）」；
> 样本不够就明确弃权，不硬给结论。
>
> 本文是规则的唯一口径来源，代码实现与测试都以本文为准。

## 1. 为什么单点压差分不清脏了还是该换

床层压降近似服从 Ergun 形式：

```
Δp = μ·(a·Q) + ρ·(b·Q²)
```

- `a·Q` 为粘性（层流）项，悬浮物堵塞会使其上升；
- `b·Q²` 为惯性（湍流）项，滤料级配恶化、板结、跑料会改变其形态；
- 单点 `(Q, Δp)` 同时受「当前流量、水温粘度、床层脏污、滤料老化」影响，无法归因。

因此必须在**同一轮次内采多个流量点**，拟合出床层自身的阻力曲线，再在统一的
参考流量、参考水温下折算一个与运行流量无关的标量——**自身阻力**。

## 2. 数据模型

### 2.1 样本 `BedSample`

| 字段 | 含义 | 约束 |
|---|---|---|
| `flow` | 瞬时流量 Q | > 0 |
| `drop` | 床层压差 Δp | ≥ 0 |
| `temperature_c` | 水温 ℃ | 默认 20 |

样本应在稳态下采集（稳态判定由采集侧负责，判定层只消费样本）。

### 2.2 温度/粘度归一化（`viscosity.py`）

水的动力粘度按 0–40 ℃ 手册表线性插值，越界钳制到端点。所有压差先折算到
**20 ℃ 参考粘度**：

```
Δp_norm(Q, T) = Δp(Q, T) · μ(20℃) / μ(T)
```

密度在该温区按常数处理。这样冷水造成的高粘滞压降不会被误判成床脏。

### 2.3 阻力曲线拟合（`fit.py`）

对归一化样本做**过原点的两参数最小二乘**：

```
Δp_norm = a·Q + b·Q²
```

拟合前必须依次通过以下数据质量闸门，任一不过即**弃权**：

| 闸门 | 默认阈值 | 弃权原因码 |
|---|---|---|
| 样本数 | ≥ 3 | `too_few_samples` |
| 不同流量数 | ≥ 3 | `too_few_distinct_flows` |
| 流量量程 max/min | ≥ 1.5 | `flow_span_too_narrow` |
| 参考流量被样本区间包住 | Qmin ≤ Qref ≤ Qmax | `reference_flow_not_bracketed` |
| 同轮温差 | ≤ 10 ℃ | `temperature_spread_too_wide` |
| 拟合优度 R²（过原点口径） | ≥ 0.98 | `poor_fit` |
| 系数物理性 | a > 0 且 b > 0 | `non_physical_fit` |

闸门不过时返回 `FitRejection`，**绝不返回一条不可信的曲线**。

### 2.4 自身阻力标量

```
R = a·Qref + b·Qref²        # 参考流量 Qref=80、参考水温 20℃ 下的归一化压降
```

### 2.5 标定基准 `BaselineVersion`

干净、刚洗好、健康床层的曲线 `(a0, b0)`，连同参考流量一起标定，形成**不可变版本**：

```
version, a, b, reference_flow, created_tick, description
```

基准自身阻力 `R0 = a·Qref + b·Qref²`。判定时用比值 `ratio = R / R0`。

## 3. 判定口径（`decision.py`）

判定以「轮次（round）」为单位。轮次分两种：

- `service`：两次反洗之间的服役轮；
- `post_wash`：反洗后立即进行的恢复性验证轮。

| 轮次 | 条件（相对当前基准） | 结论 |
|---|---|---|
| service | ratio ≥ **1.5**（`wash_ratio`） | **wash 该洗** |
| service | ratio < 1.5 | **normal 正常** |
| post_wash | ratio < **1.3**（`replace_ratio`） | **normal 洗后恢复** |
| post_wash | ratio ≥ 1.3，连续失败次数 < 2 | **wash 再洗/检查**（第 1 次） |
| post_wash | ratio ≥ 1.3，且同一基准下**连续 2 次**（`required_failed_recoveries`） | **replace 该换** |
| 任意 | 无可用曲线 / 无有效基准 | **abstain 弃权** |

关键区分：

- **服役中阻力升高只能判「该洗」，绝不直接判「该换」**。单条曲线无法区分
  悬浮堵塞与滤料老化，必须靠「洗后能否恢复」这一额外证据。
- **滤料老化的证据是反洗后不可恢复**：洗后自身阻力仍 ≥ 1.3 R0，且连续两个
  反洗周期如此，才判 `replace`；任意一次洗后恢复（ratio < 1.3）立即清零计数。
- 弃权原因码全部机器可读：`no_active_baseline`、`too_few_samples`、
  `too_few_distinct_flows`、`flow_span_too_narrow`、
  `reference_flow_not_bracketed`、`temperature_spread_too_wide`、`poor_fit`、
  `non_physical_fit`。

> 第四种结论 `normal`：除要求的 wash / replace / abstain 外，健康床需要一个
> 明确的「正常」出口，避免把「不用动作」和「判不了」混为一谈。若只需三态，
> 展示层可把 normal 归入「无需处理」。

## 4. 标定换版＝迁移，历史不可变（`baseline.py` / `service.py`）

这是本设计的核心约束，定死如下：

1. **基准只增不改。** 每个标定是独立不可变版本；同一版本号重复注册一律拒绝
   （`BaselineError`），旧基准永远可被追溯取到。
2. **轮次在开启时钉死版本（pin）。** 轮次记录 `baseline_version`，整轮（拟合、
   判定、落库）始终按该版解释。
3. **换版请求在轮次进行中只排队，不立即生效。**
   - 有开放轮次：`activate` 返回 `queued`，版本置于 `pending`，运行轮继续用旧版；
   - 无开放轮次：立即生效，返回 `activated`；
   - 同时只允许一个待生效版本，第二个不同版本的排队请求被拒绝。
4. **换版只在轮次边界生效。** 轮次关闭、判定落库之后，才提升 `pending`；
   `backwash()` 本身就是边界：先按旧版关闭服役轮，再提升新版，**洗后验证轮在
   新版下开启**。
5. **已出结论历史不可变。** 判定记录一旦写入即为只读；换版、新样本都不得
   回头改写旧结论。旧结论永远带它当时的 `baseline_version`，按那一版解释。

### 4.1 「正在跑的那一轮」这条规则（不可再议）

> **正在跑的那一轮，一律按旧版算完；新版只对边界之后开启的下一轮生效。**

不存在「立即改用新版」。原因：判定必须对一轮完整样本自洽，中途换版会让同一条
曲线前后口径不一，也违背历史不可变。反洗动作天然充当边界（见第 3 节流程）。

## 5. 轮次生命周期

```
open_round(service)
   └─ add_sample()*                 # 多流量稳态样本
close_round()  ──► VerdictRecord     # 判定并永久落库（边界：提升 pending）

backwash()
   ├─ 若 service 轮开放：先 close_round()（按旧版出结论）
   ├─ 边界：提升 pending 基准
   └─ open_round(post_wash)
         └─ add_sample()* → close_round()   # 洗后恢复验证
```

- 同时只允许一个开放轮次；重复开启、无轮次加样、post_wash 中再反洗都报
  `RoundError`（HTTP 409）。
- 轮次号、开放轮、结论都持久化在 Store，进程重启后从序列号继续编号。

## 6. 控制台展示 与 对外取数：同一事实源

- 控制台与对外数据接口**投影同一份不可变记录**，不存在两份会漂移的结论：
  - `GET /api/filter/verdict`：最新一条结论（对外取数入口）；
  - `GET /api/filter/verdicts`：全部结论（旧→新）+ 当前开放轮的**非约束预览**；
  - 开放轮预览带 `"final": false`，仅供实时展示，不代表已出结论。
- 写接口：`POST /api/filter/round|sample|close|backwash`、
  `POST /api/filter/baselines`（注册/列表）、
  `POST /api/filter/baselines/activate`（换版）。
- 每条结论字段：`round_no, kind, verdict, baseline_version, ratio, curve,
  reason_code, reason_detail, samples, opened_tick, closed_tick, prev_hash,
  record_hash`。

## 7. 审计一致性核对（`audit.py`）

审计只读、独立复算、**只报问题不改历史**。`POST /api/filter/audit` 对每条记录：

1. **哈希链**：记录内容 SHA-256（含前一条哈希 `prev_hash`），任一字段被改写即
   `hash_mismatch`，断链即 `chain_broken`；
2. **独立重拟合**：用记录内冻结的原始样本重新跑一遍拟合闸门和最小二乘，与存储的
   `a/b/R` 比对，不符即 `curve_mismatch` / `fit_status_mismatch`；
3. **按当时版本复算判据**：取记录自己的 `baseline_version`（不是今天的活动版），
   重放该版下的连续洗后失败计数，重新 `decide`，比对 `verdict / ratio /
   reason_code`；
4. **审计事件口径**：每个 `filter_verdict` 结论对应一条审计事件，数量不符即
   `audit_event_mismatch`；
5. 基准缺失、引用版本不存在：`missing_baseline`。

`ok=false` 时给出 `findings` 列表，由人工处置；审计从不自动「修复」旧记录。

## 8. 存储键与事件

- 键命名空间 `filter/bed/<bed>/...`（默认床 `main`，已预留多床）：
  `baselines`、`round/open`、`round/sequence`、
  `verdict/<no>`、`verdict/latest`、`postwash_failures/<version>`。
- 审计事件 kind：`filter_round_opened`、`filter_verdict`、`filter_backwash`、
  `filter_baseline_registered`、`filter_baseline_change_queued`、
  `filter_baseline_activated`。

## 9. 可调参数（默认值，变更需走标定流程而非改历史）

| 参数 | 默认 | 含义 |
|---|---|---|
| 参考流量 Qref | 80 | 折算自身阻力的统一流量 |
| 参考水温 | 20 ℃ | 粘度归一化基准 |
| wash_ratio | 1.5 | 服役轮该洗阈值 |
| replace_ratio | 1.3 | 洗后未恢复阈值 |
| required_failed_recoveries | 2 | 连续洗后失败几次判换 |
| min_samples / min_distinct_flows | 3 / 3 | 最少样本/流量点 |
| min_flow_ratio | 1.5 | 流量量程要求 |
| max_temperature_spread | 10 ℃ | 同轮温差上限 |
| min_r_squared | 0.98 | 拟合可信度下限 |
