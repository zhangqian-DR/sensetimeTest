# 对《月度核算账本实施任务清单》的 Review 意见

> 评审对象：你（Codex）给出的实施任务清单（含静态问题清单、12 个任务、待决策边界）。
> 评审依据：题面 README 全文 + Starter Code 全部源码静态走读（尚未运行测试）。
> 结论：清单整体合格，12 项静态问题逐条核对均属实，且抓到了三个真正的题眼（规则按 `now` 而非 `work_date` 匹配、float+银行家舍入、不同幂等键并发核算）。但存在 **四类需要处理的问题：关键边界遗漏、个别技术落点未讲透、三处过度设计、测试排期过晚**。请按本意见修订后再开工。

---

## 一、必须补进清单的遗漏点

| # | 遗漏点 | 代码位置 | 正确处置 |
|---|---|---|---|
| 1 | **退回 ACTIVE 的确认语义未定义** | `period_service.py:18`（允许 `PENDING_CONFIRM→ACTIVE`） | 退回时保留已有 CONFIRMED（金额未变），由后续重算统一走金额失效逻辑；不得在退回时静默清空确认。在 DESIGN 写明。 |
| 2 | **"当前版本成功核算"的判定口径不明** | `period_service.py:80` 前置检查 TODO | 必须存在 `CalculationRun.status=SUCCEEDED` **且其 `period_version` 等于周期当前版本**；解锁后旧版本的 SUCCEEDED 不能算数；RUNNING/FAILED 不算。 |
| 3 | **A4 的真正技术落点没说清** | 解锁后 `version+1` | 旧版本 entry/settlement 是 `period_version` 不同的独立行，天然只读保留；A4 实际决策的是"**新版本 settlement 行初始化时，是否从旧版本继承 CONFIRMED**"。继承会使 `confirmed_version` 与当前版本不一致，违反 §6.4"确认绑定当前版本"。建议：新版本一律 PENDING、全部重新确认（详见第四节）。 |
| 4 | **A5 必须区分两种"零元"** | `settlement_service.rebuild` | (a) 从未产生 entry 的人/任务：**不生成 settlement 行**；(b) 有 entry 但合计 0（红牌/旷工）：**生成 0 元行且必须正常确认**。增量导致最后一条 entry 消失时，**删除该 settlement 行**（孤儿结算），不要留 0 行。 |
| 5 | **`_confirm` 重新确认残留字段** | `settlement_service.py:333-340` | INVALIDATED 再次确认时清了 `invalidated_at/reason`，但漏清 `previous_confirmed_amount`，会残留上一次失效金额。补上清理。 |
| 6 | **`dirty` 语义与"未计费记录"阻塞混在一起** | `calculation_service.py:178-206` | 当前只有成功出 entry 的记录才 `dirty=False`，NO_RATE_RULE / UNDEFINED / PENDING 记录永远脏，会导致周期永远进不了 PENDING_CONFIRM。正确做法：**核算动作完成后，凡处理过的记录（含跳过）一律 `dirty=False`**——`dirty` 只表示"自上次核算后输入是否变更"；进入待确认的前置检查改为独立的三项实时查询：① 仍有 `dirty=True`；② 仍有 `performance=PENDING`；③ 存在"周期内有 attendance 但当前版本无对应 entry"的记录（left join 反查，覆盖 NO_RATE_RULE/UNDEFINED，防止漏薪）。三者分别返回不同错误码。 |
| 7 | **A2 是模型陷阱，不是定个系数就能解决** | `rate_rule_service.py:13-21` | `ABSENT_FORCE_MAJEURE` 即使系数=1.0，DAILY 公式 `base=日薪×min(hours,8)/8`，缺勤 `hours=0` 仍算出 0。必须连带决定：该等级 DAILY 按 8h 计（或业务当天照常记 8h）。DESIGN 与测试都要覆盖这一点，否则系数定义了也不生效。 |
| 8 | **PUT 允许把成绩改回 PENDING** | `schemas/ledger.py:23` | 已出成绩改回 PENDING 会凭空制造"成绩未出"并要求删除对应 entry。需明确口径：建议允许（与其他修改同等对待：version+1、标脏、重算后 entry 被移除），但要有对应测试；或直接 422。二选一写进 DESIGN。 |
| 9 | **质检批次需保证整批原子性** | `review_service.py` | 批内任一条失败（404/日期非法/422）必须整批回滚，不得留下部分 ReviewResult 与部分 version 自增；重放成功返回首次结果且 `accepted` 口径稳定。 |
| 10 | **归档后写入拒绝的覆盖面要列全** | 多个 service | 必须拒绝：出勤 PUT、质检 batch、核算（现有 `PERIOD_NOT_CALCULABLE` 已挡）、confirm/cancel（状态门禁挡）、transition（`ALLOWED_TRANSITIONS` 无 ARCHIVED 键，已挡）。GET 类查询照常开放。逐路径在"锁内重读 status 后判定"，不要只靠进入接口时的一次查询。 |

---

## 二、三处过度设计，请降级或删除

1. **不要给指纹加单价/价格 hash。** 题面 §6.2.4 已把指纹字段锁死为那 9 个（含 `rule_id`，不含价格）。正确做法是声明 **RateRule 不可变（immutable）：调价=新增一条带生效区间的规则行**，`rule_id` 变化自然被指纹捕获。你清单中"原地改价无法识别"的担忧，用"规则禁止原地修改 + 服务层不提供改价接口（Starter 本就没有）"即可关闭，不要扩展指纹。

2. **规则重叠的数据库 exclusion constraint（GiST + daterange）降为可选。** 它是 PostgreSQL 专属、SQLite 不支持。本题在"周期锁内复查 + 现有 `assert_no_overlap`"下已足够；exclusion constraint 只作为生产加固，不阻塞交付。

3. **跨批次乱序 / 复杂裁决降级。** 题面只硬性要求"同一批次可安全重放"（§6.3）。做到：`(batch_id, attendance_record_id)` 唯一约束 + 重放返回首次结果；跨批次以最后应用者为准（version 单调递增）；批内同一 `attendance_record_id` 出现多次建议直接 422。不需要版本向量或乱序状态机。

---

## 三、六个重点技术问题的明确答复

### 1. 周期级并发控制如何选型

- **主方案：以 `salary_periods` 行锁（`SELECT … FOR UPDATE`）作为周期互斥锁**，在事务开头获取。PostgreSQL 下是真行锁；SQLite（StaticPool 单连接）退化为串行，语义一致、无需 PG 专属语法，公开测试可跑。
- **PG 增强（可选）：叠加 `pg_advisory_xact_lock(hashtext(period_id))`**（题面 §8 明确提示了咨询锁）。
- **幂等键兜底**：保留 `(organization_id, idempotency_key)` 唯一约束；并发插入时 `catch IntegrityError → 重查该 run → request_hash 相同则返回首次快照、不同则 409 IDEMPOTENCY_KEY_REUSED`；RUNNING 跨事务可见时返回 409 CALCULATION_IN_PROGRESS。当前代码"先 select 后 insert"在并发下第二个请求会把 IntegrityError 直接放成 500，必须修。
- **归档二次保险**：锁内判 status 之外，再用条件更新 `UPDATE salary_periods SET status='ARCHIVED' … WHERE id=? AND status='PENDING_CONFIRM'`，影响行数=0 即 409，保证两个管理员并发只成功一次、SYSTEM 代确认与日志不重复。

**必须获取同一把周期锁的写路径**（同一 period 内）：FULL/INCREMENTAL 核算（含不同幂等键并发）、transition、archive、unlock、confirm/cancel；出勤 PUT / 质检 batch 先按 `work_date` 定位所属周期，再取该周期锁（避免核算读源数据期间被并发改）。定位不到周期的记录按 A6/边界口径处理。

### 2. 对账能可靠报告什么粒度

两个视角是**同一批 LedgerEntry 的两种 GROUP BY**，数据一致时总额与人×任务矩阵在数学上必然相等。题面示例的单元格级 `mismatches{annotator_id,task_id,annotator_view,task_view,diff}`，在"单一账本来源"前提下**正常状态恒为空，也不应能构造出"总额相等但单元格对不上"的内部状态**。

对账应实现为**三方核对**，报告三类客观可证差异：
1. **settlement 漂移**：某 settlement 行金额 ≠ 其名下 entry 重新聚合值（按人、按任务逐个比，可定位 id）；
2. **总额不等**：`annotator_total ≠ task_total`；
3. **孤儿**：有 entry 但缺 settlement 行，或有 settlement 行但当前版本无 entry。

保留题面要求的 `mismatches` 结构（一致时为空数组）。**"非平凡对账"测试的正确构造方式：手工篡改一条 settlement.amount 或删除一个 settlement 行，断言接口报告出对应 diff**，而不是去构造不可能存在的单元格分歧。

### 3. 增量核算如何兼顾正确性、局部更新与 SQL 上限

目标：仅改 1 条出勤时，SQL 条数与周期总记录数**常数级无关**。

1. 取周期行锁。
2. 一次 SELECT 拉周期内 attendance（必要时只拉 `dirty=True`），一次 SELECT 拉当前版本 ledger 的**轻量投影**（`source_record_id, fingerprint, annotator_id, task_id, amount`）；在内存做集合差，得到：新增 / 指纹变化 / 消失（含"原可计薪→变为 NO_RATE_RULE/PENDING/UNDEFINED"）。
3. **规则批量预加载到内存做区间匹配，严禁逐条 `resolve_rule`——这是最容易触发 SQL 条数上限的 N+1**。
4. 受影响 entry：`source_record_id IN (...)` 一条 DELETE + 一条批量 INSERT。
5. 受影响人/任务集合 = 变更前后归属取并集（含被删 entry 的旧归属）；**只对这些 id 按 entry 重算其 settlement，禁止复用全量 `rebuild`（它会全表拉取）**。
6. 金额更新与确认失效统一走现有 `_apply_amount`（FULL/INCREMENTAL 共用，保证失效路径不遗漏，两视角各自判断）。
7. 未变化的 entry 与 settlement 一律不碰；孤儿结算按第一节 #4 处理。
8. 用 `sql_counter` 实测"改 1 条"的 SQL 条数并记录在 DESIGN；注意计数器统计的是语句数不是行数，全表投影只算 1 条，N+1 才会爆。

**关于"孤儿明细"的现实来源**：Starter 没有删除出勤、没有改 work_date/task 的接口，正常 API 下"记录被删除/移出周期"不会发生。增量真正要清理的是**状态翻转导致旧 entry 失效**（规则切换、成绩 PENDING 翻转、系数变化）；"source_record 不存在 / work_date 移出周期"作为防御性集合差保留，其测试直接用 db session 改库构造。

### 4. 哪些必须在 PostgreSQL 验证

- **SQLite 足够**：金额与 ROUND_HALF_UP（在 Python 层量化，与 DB 无关）、指纹、状态机、权限/404、串行幂等重放、归档/解锁流程、版本与历史保留、对账差异、孤儿清理、SQL 条数。
- **必须/强烈建议 PostgreSQL**：同幂等键并发的 IntegrityError 路径、不同幂等键并发核算的锁等待与串行化、两管理员并发归档只成功一次、`FOR UPDATE`/advisory lock 的真实阻塞、（若做）exclusion constraint。并发测试用 `skipif(未设置 TEST_DATABASE_URL)` 保护，SQLite 下保留"唯一约束冲突"的串行近似测试。

### 5. 金额与舍入

- 全程 Decimal，禁止 float 中转；`compute_amount` 改为 Decimal 运算并在**单条明细级** `.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`。
- 当前 `round(base*coef, 2)` 是 Python 银行家舍入（ROUND_HALF_EVEN），且 float 有二进制误差，与题面 §5.3"四舍五入"不符。
- settlement 直接 `sum` 已舍入的 entry，两视角同源，恒等为数学必然；seed 期望值（526/595/280/790/611/1401）即此口径。

### 6. 北京时区

- `to_business_date` 必须先把 aware datetime 转到 UTC+8 再取 `.date()`，不能直接 `dt.date()`。否则 UTC 16:00 之后（北京已跨日）的 reviewed_at 会错算业务日期，进而让"质检日期不得早于出勤业务日期"误判。存储仍统一 UTC。

---

## 四、A1～A7 建议口径（可调整，但每个决定必须配测试）

| # | 建议决定 | 理由 |
|---|---|---|
| A1 离职当天 | 在职区间按闭区间 `[active_from, deactive_from]`，离职当天计薪；`work_date > deactive_from` 的记录核算时跳过并记录原因（或在写入时拒绝） | Starter 核算当前完全没关联在职区间，需新增 |
| A2 不可抗力 | 系数 1.0，且明确 DAILY 当天按 8h 计 | 否则 hours=0 时系数 1 仍为 0（见第一节 #7） |
| A3 舍入层级 | 单条明细级 ROUND_HALF_UP，settlement 求和不再舍入 | 最细粒度、确定性最强、两视角天然恒等 |
| A4 解锁后未变者 | 新版本一律 PENDING、全部重新确认，不继承旧确认 | 最保守，避免 confirmed_version 与当前版本不一致；归档时 SYSTEM 代确认逻辑可原样复用 |
| A5 零元结算 | 无 entry 不建行；有 entry 合计 0 建行且需确认；条目算光则删行 | 区分"未参与"与"参与了但为 0" |
| A6 待确认阶段修改 | **严格派**：PENDING_CONFIRM 拒绝改源数据（409），要求先 transition 回 ACTIVE 再改 | 消灭"重算前确认名存实亡"的窗口，状态机最干净；若选宽松派（允许+标脏），则必须配合 SETTLEMENT_STALE 并在 DESIGN 说明陈旧确认窗口 |
| A7 修改后是否立即重算 | **不自动重算**，只 version+1、标脏，由管理员显式触发增量核算 | 核算是带幂等键的独立资源，自动触发会破坏审计清晰度且在批量回写下性能极差 |

---

## 五、任务顺序调整

你的主干顺序合理，但**测试不能排到最后的任务 11**。本题测试驱动、§7 要求每个决策挂一个测试名，后置补测试会返工。调整为：

1. **先做基线（原任务 3）**：建环境、跑原始 12 个测试记录结果，确认起点全绿。
2. **决策（原任务 2）与并发骨架（原任务 4 核心）并行先行**：先落地"统一事务模板 + 周期行锁助手 + 各唯一约束"，后续所有 service 复用同一套，避免各写一套锁。
3. **修基础核算（任务 5）时立即配"规则版本化"测试做护栏**：构造方式——把 FixedClock 推到新规则生效日之后、work_date 落在新规则生效日之前，验证仍按 work_date 选旧规则（旧代码用 today 会误选新价）。
4. 之后按 6（写路径）→ 7（增量）→ 8（确认）→ 9（归档/解锁）→ 10（对账/审计）推进，**每个任务自带测试并即时跑**。
5. 任务 11 收缩为收尾项：全量/增量等价、孤儿清理、SQL 条数实测、完整 smoke（`CHECK_COMPLETION=1`）、六类必测与 A1–A7 覆盖核对。

---

## 六、交付前硬清单（逐条自检）

- [ ] 新增测试 ≥ 6 且分别覆盖：规则版本化 / 非平凡对账（篡改或删行造差异）/ 失效金额变与不变（不变场景：改动只影响他人，本人确认保持）/ 解锁版本与历史只读 / 并发或模拟并发 / 一个 A 决策。
- [ ] A1–A7 每一项都能在 DESIGN 指到具体测试名。
- [ ] `pytest -q` 全绿；`CHECK_COMPLETION=1 pytest tests/test_contract_smoke.py -q` 全绿。
- [ ] 改 1 条出勤后跑增量，用 `sql_counter` 量过 SQL 条数且与周期数据量无关（数字写进 DESIGN）。
- [ ] 归档并发、同键/异键核算并发在 PostgreSQL 验证过；无 PG 时至少用唯一约束冲突路径做串行近似。
- [ ] 审计日志覆盖：核算、出勤修改、质检回写、确认、取消、失效、状态切换、归档、解锁，载荷含版本/操作者/金额前后值/原因。
- [ ] SYSTEM 代确认使用 `app/core/current_actor.py` 已定义但目前闲置的 `SYSTEM_ACTOR_ID`，且不覆盖人工 SELF/PROXY 确认。
- [ ] `LedgerEntry` 增加 `(period_id, period_version, source_record_id)` 唯一约束；`ReviewResult` 增加 `(batch_id, attendance_record_id)` 唯一约束（测试经 `create_all` 自动生效，无需强制 alembic）。
- [ ] DESIGN.md **不超过 1500 字**，回答题面 7 个问题；AI_LOG.md ≥ 4 条且至少 1 条真实否决/大幅修改（写明照搬会导致的问题），不得编造。

## 七、必须 vs 可选总表

**必须修正（不修则功能缺失/数据错误/测试挂）**：work_date 匹配规则；纯 Decimal + ROUND_HALF_UP；增量/归档/解锁三个 501；进入待确认三前置（含按版本判 SUCCEEDED）；确认/取消状态门禁与 SETTLEMENT_STALE；写路径归档门禁；质检批次幂等去重与整批原子性；全量/增量/解锁所有重算路径的两视角失效；归档 SYSTEM 代确认 + 并发只成功一次 + 归档后拒写；解锁版本递增/旧版只读/A4；对账三方核对；幂等 IntegrityError 重放 + 周期锁；北京时区；两处唯一约束；9 类审计日志；第一节 #5/#6/#10 的修正。

**可选优化（不阻塞，按时间取舍）**：PG 规则 exclusion constraint；规则原地改价检测（改为声明 immutable 即可）；跨批次乱序复杂裁决；task_owner 对账视图（维持 admin-only 即可）；alembic 迁移（测试靠 create_all，PG 生产再补 SQL）；请求校验异常中 Decimal 的 JSON 序列化（`exc.errors()` 的 input 可能是 Decimal）；周期日期重叠的 DB 约束；清理 `previous_confirmed_amount` 残留（虽列在必须，但改动极小，可随确认功能一起做）。
