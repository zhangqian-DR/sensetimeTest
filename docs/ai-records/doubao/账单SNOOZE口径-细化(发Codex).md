# SNOOZE（明天再提醒）口径细化 — 请 Codex 评估是否调整

> 背景：账单跟进系统已完成、78 测试全绿、10 场景 PASS。Review 中发现**唯一一处与题面可能存在解释张力**的业务口径，集中在"明天再提醒"。本文只做问题细化与方案建议，**当前未改任何代码**，请 Codex 判断是否采纳、采纳哪一种。

---

## 1. 一句话问题

用户在问题**尚未确认有问题（status=OPEN）**时只说"明天再提醒"，当前实现到期发送 1 条后，会把跟进模式置为 `WAITING_FACT`，导致**自动催办链永久中断**；而题面"延期不重置次数"的语义，更像是次数保留、到点后继续按"未回复"追加。

---

## 2. 现状代码路径（`app/service.py`）

### (a) SNOOZE 动作：不区分问题状态，统一置 USER_SCHEDULED

```python
# service.py 235-244
elif action=='SNOOZE':
    target=now+24*HOUR if p.get('tomorrow') else p['remind_at']
    if target<=now:
        raise DomainError(422,'remind_at must be in the future')
    issue.not_before_at=max(target,issue.not_before_at or 0,
                            (issue.last_sent_at+24*HOUR) if issue.last_sent_at is not None else 0)
    issue.requested_remind_at=issue.not_before_at
    issue.schedule_generation+=1
    issue.followup_mode='USER_SCHEDULED'        # ← 无论 status 是 OPEN 还是 CONFIRMED_PROBLEM
    self._cancel(session,issue,'Replaced by new user agreement')
```

### (b) 到期创建并发送 USER_SCHEDULED 任务

```python
# service.py 334-335
if issue.followup_mode=='USER_SCHEDULED' and issue.requested_remind_at is not None:
    kind,key,due='USER_SCHEDULED','USER:'+str(issue.schedule_generation),issue.requested_remind_at
```

### (c) 发送成功后：置 WAITING_FACT

```python
# service.py 408-414
elif task.kind=='USER_SCHEDULED':
    generation=int(task.task_key.split(':')[1])
    if generation==issue.schedule_generation:
        issue.requested_remind_at=None
        if issue.status not in TERMINAL:
            issue.followup_mode='WAITING_FACT'   # ← 自动催办到此终止
```

### (d) 后续评估：WAITING_FACT 不匹配任何会创建任务的分支

```python
# service.py 334/336/345
if issue.followup_mode=='USER_SCHEDULED' ...:   # 否
elif issue.followup_mode=='AWAITING_REPLY':     # 否
else:
    continue                                    # ← 不再自动追加
```

**结果**：USER 提醒发完后，无论用户是否回复，都不再有 24/48h 自动追加。

---

## 3. 与题面的对照（原文）

- 第 4 条："首次提醒不计追加次数。**未回复时距上次至少 24 小时追加，最多两次**；部分到账等新数据不重置次数。**用户确认有问题后**等相关事实或下一项明确约定，**不沿用无回复催办**。"
- 第 5 条："明天再提醒默认延后 24 小时，与既有最早提醒时间取较晚值；明确日期需澄清时间。**延期不重置次数**。"
- 第 3 条把"明天再提醒"与"确有问题"列为**两个相互独立**的用户标记。

**张力点**：
- "不沿用无回复催办"在题面里的触发前提是"**用户确认有问题后**"；
- 而"明天再提醒"是独立动作，用户并**没有**确认问题，问题仍是 OPEN；
- 对 OPEN 状态的纯延后，"延期不重置次数"自然的理解是：次数保留，到点发送后，若仍无回复，**继续**按 24h 节奏追加（直到 2 次上限）。
- 当前实现对"纯延后"也套用了"确认问题后等事实"的处理，自动追加不再继续，"不重置次数"保留下来的次数实际不再被使用。

---

## 4. 两种口径对比

| | 口径 A（当前实现） | 口径 B（按"未回复继续"严格理解） |
|---|---|---|
| OPEN 下 SNOOZE 到期发送后 | WAITING_FACT，停止自动追加 | 回到 AWAITING_REPLY，继续自动追加（受剩余次数限制） |
| CONFIRMED_PROBLEM 下约定到期 | WAITING_FACT，停止 | WAITING_FACT，停止（两者一致） |
| 自动追加总次数 | 可能少于 2 次即停 | 严格用满到 2 次上限（若用户始终不回复） |
| 优点 | 简单；用户主动介入后不"打扰" | 与"延期不重置次数 + 未回复继续追加"字面一致 |
| 风险 | 与题面第 4/5 条一种合理解释有张力，可能被追问 | 延后到期后仍会继续催，需确认这是否符合预期 |

**关键区分维度**：SNOOZE 约定**到期发送那一刻** issue 的 `status`：
- `CONFIRMED_PROBLEM` → 属于"确认问题后的约定"，到期 `WAITING_FACT`（不变）；
- `OPEN`（未确认）→ 属于"纯延后"，到期应回到 `AWAITING_REPLY`。

> 用"到期时的 status"而非"操作 SNOOZE 时的 status"，可顺带覆盖"延后期间用户又确认了问题"的情况，更稳健。

---

## 5. 建议的最小修改（如采纳口径 B，仅供参考）

仅需改 `_complete` 中 USER_SCHEDULED 分支（service.py 408–414），按到期时 status 决定回落模式：

```python
elif task.kind=='USER_SCHEDULED':
    generation=int(task.task_key.split(':')[1])
    if generation==issue.schedule_generation:
        issue.requested_remind_at=None
        if issue.status not in TERMINAL:
            # 已确认问题 -> 等事实；仅延后(OPEN) -> 回到未回复跟进，继续自动追加
            issue.followup_mode = ('WAITING_FACT' if issue.status=='CONFIRMED_PROBLEM'
                                   else 'AWAITING_REPLY')
```

说明：
- 回到 `AWAITING_REPLY` 后，`initial_sent_at` 已存在（首次一般已发），后续自然走 AUTO 分支；若首次未发成功（FAIL 导致 initial_sent_at 为 None），会重新走 INITIAL，也合理，无需特殊处理。
- `not_before_at` 已在 SNOOZE 时更新，AUTO 的 `due=max(last_sent+24h, not_before)` 计算正确，不会被卡住。
- 不需要新增表字段。

---

## 6. 受影响 / 需新增的测试（请 Codex 核对）

**会改变断言的现有测试：**
- `tests/test_business.py::test_snooze_and_explicit_after_quota`
  - 时间线：pair（首次）→ 24h（AUTO1，count=1，OPEN）→ SNOOZE tomorrow（约定到期≈48h）→ 48h（USER 发送）→ 96h。
  - 按口径 B：48h 回到 AWAITING_REPLY、count=1，96h 时 AUTO2（due=72h）会发送。
  - 现有断言 `96h 时 notifications==3` 会变为 **4**；`count` 最终为 **2**。需同步更新该断言。

**不受影响的现有测试（已核对）：**
- `test_explicit_reminder_after_auto_quota`：SNOOZE 时 count 已=2（满），即使回 AWAITING_REPLY 也不再追加，现有 `count=2、notifications=4` 仍成立。
- `test_close_before_due_submit`：SNOOZE 后立即 CLOSE，任务被 CANCELLED，不涉及到期。
- 其余 SNOOZE/重放/暂停测试不涉及"到期后是否继续"。

**建议新增的测试（明确锁定口径）：**
- OPEN 状态 SNOOZE：到期发送 1 条 USER（不计自动次数），再推进 24h，用户仍不回复 → 再发 1 次 AUTO、count 递增；推进到用满 2 次后停止。
- CONFIRMED_PROBLEM 状态 SNOOZE：到期发送后保持 WAITING_FACT，不再自动追加（锁定"确认问题后不催"）。
- 若 demo 场景 7 的时间线推进到了约定到期之后，需同步确认 `demo.py scenario_7` 与 `reports/acceptance.md` 的断言；若场景 7 只推进到约定到期，则不受影响。

---

## 7. 请 Codex 决策

1. 采纳**口径 A（保持现状）**还是**口径 B（区分 OPEN / CONFIRMED_PROBLEM，按第 5 节修改）**？
2. 若采纳 B：请确认第 6 节列的受影响测试是否完整，补新增测试，并复跑 `pytest -q` 与 `demo.py --managed-server`，更新 `reports/` 与 DESIGN 口径说明。
3. 若采纳 A：建议在 DESIGN 中明确写一句"用户主动约定（含明天再提醒）到期后统一等待事实，不恢复无回复催办"，作为答辩时的口径依据。

> 无论选哪种，都不影响当前 10 场景的字面验收结果；这是一次"业务严谨度 vs 实现简洁度"的取舍。
