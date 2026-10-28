# 对《月度核算账本实施方案（第二轮）》的 Review 意见

> 评审对象：你（Codex）的第二轮实施方案。
> 评审依据：题面 README 全文 + Starter Code 全部源码静态走读（尚未运行测试）。
> 结论：**第二轮方案已收敛，可直接进入实施，没有方向性错误，也基本没有过度设计。** 第一轮意见几乎全部被采纳，且在 A5、A6 两个分歧点上，你的独立判断实际上纠正了第一轮评审的错误，这是正确的做法。剩余工作不是改方向，而是把 6 个机制点明确到可编码的程度。

---

## 一、第一轮意见的处理核对（均已正确采纳）

| 第一轮意见 | 处理 |
|---|---|
| 退回 ACTIVE 不清空已有确认，重算按最终金额判断失效 | 已采纳，正确 |
| 进入待确认要求按当前版本判 SUCCEEDED，旧版本成功记录不算数 | 已采纳，正确 |
| A4 新版本结算一律 PENDING、不继承旧确认 | 已采纳，正确 |
| 重新确认后清理失效状态字段，失效历史由操作日志保留 | 已采纳，正确 |
| dirty（输入是否变化）与"能否生成明细"分离；处理过的记录成功提交后清 dirty | 已采纳，正确 |
| 允许成绩改回 PENDING：版本递增、标脏、重算移除旧明细、之后阻止进入待确认与归档 | 已采纳，正确 |
| 质检批次整批原子性，任一失败整体回滚 | 已采纳，正确 |
| 归档后拒写覆盖：核算/出勤/质检首次应用/确认/取消/非法状态切换；GET 与纯重放可读 | 已采纳，正确 |
| 指纹不增加价格 hash；放弃跨批次乱序状态机 | 已采纳，正确 |
| 周期行锁 + 多周期批次固定锁序 + 无周期记录条件更新 + 锁内重读源记录 | 已采纳，固定锁序与锁内重读是正确的防死锁/防过期状态设计 |

---

## 二、第一轮评审的两处自我纠正（你的判断正确）

1. **A6 必须允许在 PENDING_CONFIRM 修改源数据。** 第一轮评审曾建议"严格派、拒绝修改"，这是错误的：公开测试 `test_recalculation_invalidates_confirmation_when_amount_changes` 中，周期处于 PENDING_CONFIRM，先确认再 **PUT 改出勤并断言 200**。公开测试事实上强制要求允许修改。你选择"允许 + 标脏 + 脏数据未重算时拒绝确认/进入待确认/归档"，与公开测试一致，**判断正确**。

2. **A5 "最后一条明细消失时保留零元结算行"优于"删行"。** 第一轮评审曾建议删行，但物理删除一个曾经 CONFIRMED 的结算会绕过 INVALIDATED 流程、破坏确认链；保留零元行使 CONFIRMED→（金额变化）→INVALIDATED 完整可追溯，**采纳你的口径**。

---

## 三、三个分歧点的最终裁决

### A2 不可抗力缺勤：必须二选一，消除"名实不符"

你已定系数 1.0、保留 DAILY/PIECE 公式、不自动把 hours 改为 8。但存在一个必须正视的问题：DAILY 公式 `base = 日薪 × min(hours,8)/8`，不可抗力缺勤若 `hours=0`，系数 1.0 乘出来仍是 **0**，结果与 ABSENT（系数 0）相同——此时"系数 1.0"没有实际意义，也不符合不可抗力停工通常照常付薪的常识。

请在以下两者中明确选择，不可含糊：

- **（强烈建议）** 将该等级的 DAILY base 定义为日薪（等价按 8h 计）。这是让"系数 1.0"真正成立的唯一方式，不新增会计科目，只是定义该等级的 base；测试覆盖 DAILY 与 PIECE 两种模式。
- 或保留"金额为 0"，但 **DESIGN.md 必须诚实写明"本实现中不可抗力缺勤金额为 0"，测试断言真实金额 0**；不得一边写系数 1.0 一边给人"带薪"的印象。

### A5 零元结算：口径成立，落实三条对账判定

- 有 entry 无 settlement → **错误（缺结算行）**；
- 有 settlement 无 entry 且 amount=0 → **合法零元（A5 保留）**；
- 有 settlement 无 entry 且 amount≠0 → **漂移错误**。
- 从未产生明细的人/任务不主动生成结算行；归档时 PENDING/INVALIDATED（含零元行）由 SYSTEM 一并代确认。

### A6 待确认阶段修改：允许（公开测试强制）

配套 SETTLEMENT_STALE：脏数据未重算时，拒绝确认、拒绝进入待确认、拒绝归档。窗口语义需在 DESIGN 说明：PENDING_CONFIRM 修改后标脏、重算前已 CONFIRMED 者仍显示 CONFIRMED（金额尚未变），重算后由统一逻辑失效；确认动作本身被脏检查拦截。

---

## 四、开工前必须明确的 6 个机制点

1. **幂等键跨版本**：`request_hash` 必须纳入 `period_version`（或在锁内比较 `existing.period_version != period.version` 时返回 409）。否则解锁（version+1）后用旧键提交，会被误判为重放、返回旧版本快照，用户误以为已在新版本核算。结论：**新版本核算必须使用新幂等键**。

2. **归档后只读重放**：为支持"已完成 run 在归档后返回首次快照"，需把 **existing run 的重放查询前置于周期状态门禁**。现有代码先判状态再查 run，归档后重放会先被 PERIOD_NOT_CALCULABLE 挡掉，顺序需调整。

3. **锁内复查 CalculationRun**：进入时的无锁查询在并发下不可靠；获取周期行锁后必须再查一次 existing run。保存点 IntegrityError 仅作兜底，且只处理对应约束冲突，**不得吞掉所有 IntegrityError**。

4. **质检批次必须新增一张批次记录表**（建议 `review_batches`：`organization_id, batch_id, request_hash, status, response_snapshot, created_by, created_at`，唯一 `(organization_id, batch_id)`）。方案中"记录批次摘要与首次响应"在 Starter 中没有任何表可承载——**不新增此表，"返回首次结果"无法实现**。这是必要的模型新增，不是过度设计。`ReviewResult` 唯一约束为 `(organization_id, batch_id, attendance_record_id)`。

5. **对账 `balanced` 口径**：必须等于"三方核对全部一致"（无结算漂移、无缺行、总额相等），**不能只比较 `annotator_total == task_total`**。篡改可相互抵消（某结算多 10、另一结算少 10），总额仍相等但账实不符。

6. **对账契约兼容**：保留题面 `mismatches` 字段及其原语义（人×任务单元格双视角；数据一致时恒为空，**不要伪造人×任务误差**）；三方核对发现的客观差异放入新增字段 `anomalies`（结算漂移/缺行，含 id、结算值、账本重聚合值、diff）。新增字段不违背"差异要准确报告"，把漂移硬塞进 `annotator_view/task_view` 才是伪造契约。

---

## 五、对 task_owner 对账视图的再修正（第一轮评审有误）

第一轮评审曾认为"维持 admin-only 即可"，但 Starter `reconciliation_service.py:17` 的 TODO 原文是 **"task_owner should receive a view scoped to the tasks they own"**，属作者明确意图，隐藏测试可能以 task_owner 身份调用并期望 200。因此：

- **应实现 task_owner 范围视图，而非跳过**：task_owner 调用时按 `Task.owner_id` 过滤 LedgerEntry，再在过滤后的 entry 上同时聚合两个视角，双视角仍恒等；admin 看全量。
- 这是必要项，不属于权限扩展。

---

## 六、建议项（非强制，不阻塞交付）

- 对账增加 `unbilled_records` 清单（有 attendance 但当前版本无 entry，含 NO_RATE_RULE 等原因）。你定的"NO_RATE_RULE 清 dirty、不阻止归档"**符合题面字面**（§6.1 前置仅列 dirty/PENDING，§4.3 只要求"不得静默"），不构成强制漏洞；但将未计费记录落到可查询接口，比只依赖一次性 run 快照更能落实"不静默"，建议补上。
- 明确放弃规则重叠的数据库 exclusion constraint（GiST/daterange 为 PG 专属、SQLite 不支持）；锁内复查 + 现有 `assert_no_overlap` 已足够。
- 幂等的 CONFIRMED→CONFIRMED 重复调用不写业务日志，合理；是否额外记录一条"尝试动作"日志自行决定，不强制。
- PostgreSQL 的 schema 升级路径（SQL 脚本或 alembic）与测试用 `create_all` 分开说明；`create_all` 仅用于新建库，不作为已有库升级手段，此点你已写对。

---

## 七、对你"十三、请重点复审"7 问的速答

1. **A2/A5/A6 与题面、公开测试一致性**：A6 必须允许（公开测试强制）；A5 保留零元行成立（按第三节三条判定）；A2 需在"按 8h 真正带薪"与"诚实写 0"之间二选一。
2. **跳过记录清 dirty 且不阻止归档**：符合题面字面，无强制一致性漏洞；建议加 `unbilled_records` 使漏薪可查。
3. **周期锁 / 无周期记录锁 / 多批次固定锁序**：方案完整；补充"锁内复查 CalculationRun"。
4. **幂等键跨版本重放、归档后纯重放**：合理，前提是 hash 纳入 version、重放查询前置于状态门禁。
5. **批次摘要 / 响应快照 / 结果唯一约束**：需新增批次记录表持久化摘要与首次响应；同批同内容重放、异内容 409、按 record_id 排序做摘要（顺序无关）正确。
6. **对账兼容题面且不伪造误差**：保留 mismatches 原语义，另加 `anomalies` 报告三方核对差异。
7. **保留无明细零元结算**：更利于确认失效与审计；风险在于对账须区分"合法零元"与"漂移"，判定规则已在第三节给出。

---

## 八、开工前检查清单

- [ ] A2 二选一并写入 DESIGN，配对应测试（断言真实金额）。
- [ ] `request_hash` 纳入 `period_version`；重放查询前置于状态门禁；锁内复查 run。
- [ ] 新增 `review_batches` 表与 `(org,batch_id)` 唯一约束；`ReviewResult` 加 `(org,batch_id,attendance_record_id)` 唯一约束。
- [ ] 对账实现三方核对，`balanced` 以三方一致为准，输出 `anomalies`；实现 task_owner 范围视图。
- [ ] 账本 `LedgerEntry` 加 `(period_id, period_version, source_record_id)` 唯一约束。
- [ ] 增量核算：规则批量预加载（杜绝逐条 resolve_rule 的 N+1）、受影响人/任务取变更前后并集、只重算这些结算、孤儿清理仅限当前版本；用 `sql_counter` 实测单条修改 SQL 条数并记录。
- [ ] 测试：新增 ≥6 覆盖六类（规则版本化 / 非平凡对账 / 失效金额变与不变 / 解锁版本与历史 / 并发 / 一个 A 决策），A1–A7 每项挂测试名。
- [ ] PostgreSQL 独立连接验证并发（同键/异键核算、并发归档、修改与归档竞争）；未执行时在交付中明确说明，不以 SQLite 替代证明。
- [ ] 交付：`pytest -q` 与 `CHECK_COMPLETION=1` 冒烟全绿；DESIGN.md ≤1500 字；AI_LOG.md ≥4 条且至少 1 条真实否决/大幅修改。

---

**总评**：第二轮方案无需再调整方向，也没有需要删减的过度设计。请将第四节 6 个机制点（重点：hash 纳入 version、新增批次表、balanced 口径、task_owner 对账视图、A2 二选一）固化为实施细节后即可开工。代码产出后，需以实际运行核对"方案与实现一致"，并实测增量 SQL 条数与 PostgreSQL 并发行为——这两项静态方案无法保证，必须跑起来才作数。
