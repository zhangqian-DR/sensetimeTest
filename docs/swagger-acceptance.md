# Swagger手工验收指南

本文件是操作步骤与预期结果，不代表这些手工步骤已实际执行。已执行的自动验收记录见[验收报告](../reports/acceptance.md)，运行与配置见[README](../README.md)。

## 1. 准备与注意事项

- 当前本地地址：<http://127.0.0.1:8000/docs>；健康检查：<http://127.0.0.1:8000/health>。
- 接口展开后点击 **Try it out → 填写参数 → Execute**；下方查看HTTP状态码和Response body。
- 所有金额单位为人民币“分”：3000=30元，10000=100元；时间必须带时区，本指南使用北京时间`+08:00`。
- 每次全新演示将下文所有`check-`统一换为新前缀，例如`check02-`。只有验证重放时才保留相同编号和请求内容。
- `event_id`是事件幂等编号，`tx_no`是交易编号，`arrival_no`是到账流水号。不要用修改event_id的方式重复累计同一到账事实。
- 时间推进影响当前数据库全部用户和问题；不要与其他演示共用一条不受控时间线。按问题ID筛选本次数据，不要把历史问题算作新问题。
- 不自动删除或重置数据，模拟时间不能倒退。不要为重新演示直接删除旧数据库。

### 1.1 确认服务正常

调用 **GET `/health`**，预期HTTP 200，且：

```json
{
  "status": "ok",
  "time_mode": "simulated",
  "background_running": true,
  "last_error": null
}
```

响应还包含其他字段。真实模式下不能推进模拟时间，请使用模拟模式验收。

### 1.2 用户和管理员授权

点击右上角 **Authorize**，只填Token本身，不加`Bearer`：

| 身份 | 默认演示Token | 用途 |
|---|---|---|
| 用户A | `demo-token-a` | 输入账目、处理问题、查询自己的数据 |
| 用户B | `demo-token-b` | 验证不同用户隔离 |
| 演示管理员 | `local-demo-admin-only` | 推进全局模拟时间、设置模拟通知结果 |

切换身份时先在授权窗口点击 **Logout**，再输入新Token并Authorize。管理员不等于用户，不能用管理员Token处理用户问题。若已改配置，请使用配置中的Token。

### 1.3 检查模拟时间，必要时启独立演示库

用管理员调用 **GET `/demo/clock`**。本指南退款示例假定初始时间为`2026-10-28T14:00:00+08:00`，对应`now_ms=1793167200000`。

如果当前库已推进时间，下文历史申请可能在输入后立即逾期，这是规则生效而非错误。要精确复现“推进72小时才逾期”，可以将申请确认时间改成当前模拟时间，到账时间相应调整；也可以另开PowerShell，在项目根目录启动独立文件库：

```powershell
$env:APP_TIME_MODE = 'simulated'
$env:APP_SIMULATED_START = '2026-10-28T14:00:00+08:00'
$env:APP_DB_PATH = './data/manual-check-01.db'
$env:APP_CHECK_INTERVAL_SECONDS = '1'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8002 --workers 1
```

此时打开 <http://127.0.0.1:8002/docs>，以下接口步骤完全相同。`manual-check-01.db`须是未使用的新路径；复用已存在的文件会恢复其原有时钟，不重置数据。

## 2. 疑似重复消费：发现、正常结论、重放

### 步骤1：用户A输入第一笔交易

授权`demo-token-a`，调用 **POST `/events/transactions`**：

```json
{
  "event_id": "check-t1",
  "tx_no": "check-t1",
  "merchant_id": "check-shop",
  "amount_fen": 3000,
  "occurred_at": "2026-10-28T09:00:00+08:00"
}
```

预期HTTP 200，响应`result=APPLIED`，保存返回的`receipt_id`。只有一笔时不会形成该交易对问题。

### 步骤2：输入第二笔交易

再次调用同一接口：

```json
{
  "event_id": "check-t2",
  "tx_no": "check-t2",
  "merchant_id": "check-shop",
  "amount_fen": 3000,
  "occurred_at": "2026-10-28T09:05:00+08:00"
}
```

### 步骤3：查看问题和通知

调用 **GET `/issues`**，按关联交易编号找到本次问题，复制其`id`；再调用 **GET `/issues/{issue_id}`**。

预期：

- `issue_type=DUPLICATE`，`status=OPEN`。
- 原因写明“疑似”，不能称已证实错误扣款。
- `transactions`保留并关联两笔交易，没有删除交易。
- `resolution_basis=null`，尚未解决。
- **GET `/notifications`**可查到本问题首次模拟提醒；首次后`auto_followup_count=0`。

查询 **GET `/issues/{issue_id}/history`**，可定位问题创建和提醒处理过程。

### 步骤4：用户确认是两次正常购买

调用 **POST `/issues/{issue_id}/actions`**，路径参数填刚才的ID：

```json
{
  "event_id": "check-normal",
  "action": "NORMAL"
}
```

再次GET问题，预期`status=NORMAL`，与“已解决”和“关闭”区别记录。

### 步骤5：重放交易和动作

将步骤1、步骤2和步骤4的请求原样各发送一次，保持原event_id及内容。

预期：

- 接收响应为`DUPLICATE`，不是新建业务事实。
- 本交易对仍只有一条问题，保持`NORMAL`，不重新打开。
- 两笔交易仍保留，不再次发送该问题提醒。
- 新接收仍有自己的`receipt_id`；**GET `/events/{receipt_id}`**可查看重复处理及原事件引用。

## 3. 退款：72小时逾期、部分到账、验证解决

先按1.3确认时钟；以下固定时间用于新演示库。

### 步骤1：用户A输入申请确认

授权`demo-token-a`，调用 **POST `/events/refund-confirmations`**：

```json
{
  "event_id": "check-refund",
  "refund_no": "check-r1",
  "expected_amount_fen": 10000,
  "confirmed_at": "2026-10-28T14:00:00+08:00"
}
```

预期应退100元，尚未满72小时，不应生成该退款的逾期通知。**GET `/refunds/check-r1`**可查看跟踪记录。

### 步骤2：管理员推进72小时

切换授权为`local-demo-admin-only`，调用 **POST `/demo/clock/advance`**：

```json
{
  "hours": 72
}
```

系统会自动检查，无需再调用检查按钮。若从初始时间推进，当前模拟时间应为`2026-10-31T14:00:00+08:00`。

### 步骤3：用户A查看逾期结果

切回`demo-token-a`，调用 **GET `/issues`**、**GET `/notifications`**。

预期有一条对应`check-r1`的`REFUND_OVERDUE`问题，仍欠10000分，并有首次模拟提醒。复制退款问题ID，后续持续查询同一条问题。

### 步骤4：到账60元

调用 **POST `/events/refund-arrivals`**：

```json
{
  "event_id": "check-arrival-1",
  "refund_no": "check-r1",
  "arrival_no": "check-a1",
  "amount_fen": 6000,
  "arrived_at": "2026-10-31T14:00:00+08:00"
}
```

调用 **GET `/refunds/check-r1`**，预期：

```json
{
  "received_amount_fen": 6000,
  "remaining_amount_fen": 4000
}
```

响应还有其他字段。问题仍未解决；部分到账不能重置追加次数。

### 步骤5：再到账40元

调用同一到账接口：

```json
{
  "event_id": "check-arrival-2",
  "refund_no": "check-r1",
  "arrival_no": "check-a2",
  "amount_fen": 4000,
  "arrived_at": "2026-10-31T14:00:00+08:00"
}
```

两笔到账可以发生在同一时刻，但流水号必须不同。预期累计10000分，差额0；原退款问题`status=RESOLVED`，依据`FULL_REFUND_VERIFIED`，处理历史可查。

### 步骤6：重放到账

把步骤4、步骤5原样各重发一次。预期累计仍为10000分，不是20000分；不新建问题，不重新催办。

## 4. 可选手工边界检查

各项使用独立商户/交易/退款编号，避免与上面已结案问题混用。查消息时按`issue_id`筛选，不能用整个数据库的通知总数代替本问题计数。

| 检查项 | 操作 | 预期 |
|---|---|---|
| 三笔三对 | 新商户输入09:00/09:05/09:09三笔同额交易并重放 | AB、AC、BC三条，related_issue_ids明确关联，重放不增加 |
| 自动追加 | 对新OPEN问题不回复，分别推进23h、1h、24h，再推进数天 | 未满24h不催，24/48h各追加一次，累计追加2次后停止 |
| 明天提醒 | 对未结案问题提交SNOOZE、tomorrow=true | 不早于请求时刻+24h及已有约束，计数保留 |
| 确认有问题 | 提交CONFIRM_PROBLEM后推进数天 | 仍待解决，无结案依据，停止无回复催办 |
| 关闭后到账 | 退款逾期后CLOSE，再补足额到账 | 资金事实更新，但status仍CLOSED，不继续催 |
| 暂停恢复 | PUT /me/settings传paused=true，输入事实及推进时间，再以新event_id传false | 暂停只留事实，恢复按最新事实判断，不批量补发旧消息 |
| 用户隔离 | 改用demo-token-b，查询用户A的问题ID | HTTP 404；B看不到A问题，同编号账目互不冲突 |
| 日期需澄清 | SNOOZE传remind_at="2026-11-01" | HTTP 422，要求完整时间及时区，不创建约定 |
| 原始输入追踪 | 保存每次响应receipt_id，GET /events/{receipt_id} | 可查看原输入、重复/冲突/失败原因及处理历史 |

常用动作示例：

```json
{"event_id":"check-confirm-problem","action":"CONFIRM_PROBLEM"}
```

```json
{"event_id":"check-snooze","action":"SNOOZE","tomorrow":true}
```

```json
{"event_id":"check-close","action":"CLOSE"}
```

```json
{"event_id":"check-pause","paused":true}
```

暂停示例发往 **PUT `/me/settings`**；其他动作发往对应问题的actions接口。每次新动作换event_id，重放才保持相同event_id。

## 5. 一键验证完整十场景

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe demo.py --managed-server --port 8001 --output reports/my-demo.txt
```

- 启动自己的HTTP服务子进程，每场景使用独立文件库，不影响8000端口手工演示的数据。
- 场景10实际停止并以同一库重启服务，验证状态、计数、时钟和消息保留。
- 预期`10 PASS, 0 FAIL, 0 SKIP`、进程退出码0；失败或跳过不会伪报通过。
- 证据输出为`reports/my-demo.txt`、`reports/my-demo.json`和`reports/my-demo.server.log`。
- 模拟数据库现场保留，目录在JSON报告中；不要将本地目录路径误当作别人机器上也存在的交付文件。

只验证退款场景：

```powershell
.\.venv\Scripts\python.exe demo.py --managed-server --scenario 4 --port 8001 --output reports/my-refund-demo.txt
```

完整自动测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## 6. 记录本次手工结果

验收时可复制下表填写，附接口响应、截图或输出文件；不要只写“已点过接口”。

| 场景 | 本次编号/问题ID | 预期 | 实际 | 证据位置 | 通过/失败 |
|---|---|---|---|---|---|
| 疑似重复 | 待填写 | 两笔、一条疑似问题 | 待执行 | 待填写 | 未执行 |
| 正常后重放 | 待填写 | 保持NORMAL，不新增提醒 | 待执行 | 待填写 | 未执行 |
| 退款72h | 待填写 | 到期提醒 | 待执行 | 待填写 | 未执行 |
| 60+40到账及重放 | 待填写 | 先差40，最终差0且验证解决，重放不累计 | 待执行 | 待填写 | 未执行 |