# 账单问题持续跟进系统

本地可运行的账单跟进服务：发现疑似重复消费、退款确认满72小时未足额到账后提醒，并按用户指令和新事实继续跟进。第一版通过FastAPI Swagger操作，附HTTP可重放演示，不需要自定义前端。

所有用户、交易、退款、通知均为虚构或本地模拟；不连接银行、支付、商户或真实消息平台，不执行消费、转账、删除交易等外部资金操作。金额为人民币整数分，业务时间为北京时间。



Swagger逐步手工验收：[操作步骤、请求JSON与预期结果](docs/swagger-acceptance.md)。

## 1. 干净环境安装

要求Python 3.12、可访问Python包源的安装环境，不需要额外付费服务。Windows PowerShell从仓库根目录运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

`requirements-dev.txt`包含运行依赖及pytest/httpx；HTTP演示脚本需要httpx。无需激活虚拟环境即可执行以下命令。若安装失败，应报告实际错误，不能视为已启动。

## 2. 启动和配置

```powershell
$env:APP_TIME_MODE = 'simulated'
$env:APP_CHECK_INTERVAL_SECONDS = '1'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

打开 `http://127.0.0.1:8000/docs`；健康检查为 `http://127.0.0.1:8000/health`。只能启一个worker，同一文件库不支持多个服务进程并行调度。启动时创建数据库；导入模块不建库。

配置参考`.env.example`。本版直接读取进程环境变量，不自动读取`.env`；将配置设置到启动进程后生效。修改模式需重启。

| 配置 | 默认/作用 |
|---|---|
| APP_TIME_MODE | simulated或real；非法值拒绝启动 |
| APP_TIMEZONE | Asia/Shanghai；本版固定北京时间 |
| APP_CHECK_INTERVAL_SECONDS | 1；后台检查间隔，必须正数 |
| APP_SIMULATED_START | 2026-10-28T14:00:00+08:00；只首次建模拟时钟时使用 |
| APP_DB_PATH | 模拟data/simulated.db；真实data/real.db |
| APP_TEST_TOKENS_JSON | 本地用户Token映射，可配置更多虚构用户 |
| APP_DEMO_ADMIN_TOKEN | 本地演示管理员Token，与用户Token不同 |
| APP_ENABLE_DEMO_CONTROLS | true；仅simulated时允许管理控制 |

示例测试身份：`demo-token-a`对应`user-a`，`demo-token-b`对应`user-b`；管理员示例Token为`local-demo-admin-only`。它们只用于本题本地模拟，不是任何真实服务凭据。点击Swagger的Authorize，填Token本身，无需重复填写Bearer前缀。

业务请求由Token决定用户归属，正文不能指定他人身份。管理员控制全局演示时间和通知行为，不能以此冒充用户关闭问题。

真实模式示例：

```powershell
$env:APP_TIME_MODE = 'real'
$env:APP_DB_PATH = './data/real.db'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

真实模式按实际时钟后台检查，虚拟推进被禁止。真实/模拟数据库默认分开，库内mode与配置不符时拒绝启动；不要覆盖旧数据库。schema不兼容时使用新路径或明确迁移，`create_all`不是迁移工具。

## 3. 从Swagger输入和查看

先用用户A授权，按顺序调用：

### 3.1 疑似重复消费

`POST /events/transactions`输入第一笔：

```json
{"event_id":"evt-t1","tx_no":"t1","merchant_id":"shop-1","amount_fen":3000,"occurred_at":"2026-10-28T09:00:00+08:00"}
```

第二笔：

```json
{"event_id":"evt-t2","tx_no":"t2","merchant_id":"shop-1","amount_fen":3000,"occurred_at":"2026-10-28T09:05:00+08:00"}
```

`GET /issues`查看一条疑似问题，`GET /issues/{id}`查看原因、两笔原交易、关联问题、资金事实与结案依据。`GET /issues/{id}/history`查看处理过程，`GET /reminders`查看任务，`GET /notifications`查看本地模拟收件箱。

重放同一输入会新增接收记录但不会新增交易或问题；同编号内容冲突被保留并拒绝覆盖。不同合法到账必须使用不同稳定流水号，不能靠金额和时间推测唯一性。

### 3.2 用户处理

`POST /issues/{id}/actions`支持：

| action | 含义 |
|---|---|
| NORMAL | 两次正常购买，仅重复类问题 |
| CONFIRM_PROBLEM | 确有问题，仍待解决，停止无回复催办 |
| SNOOZE | 用户明确约定提醒，传tomorrow=true或remind_at完整日期时间 |
| RESOLVE | 用户确认已自行解决，依据为用户确认 |
| CLOSE | 停止跟进，保留事实和已经发生的操作 |
| REOPEN | 用户明确重新开启，不清零历史和次数 |

```json
{"event_id":"action-1","action":"SNOOZE","tomorrow":true}
```

或指定完整时间：

```json
{"event_id":"action-2","action":"SNOOZE","remind_at":"2026-11-01T14:00:00+08:00"}
```

仅给日期、缺时间/时区将提示补充，不能擅自默认。每次新动作使用新的event_id；重放同一次动作保留同一个event_id。

首次提醒不计追加。无回复自动追加最多两次，间隔至少24小时；延期、到账和重启不清零。用户主动约定独立计为约定提醒，发完等待事实或下一次约定，不重新打开无回复催办。

### 3.3 退款逾期与分笔到账

`POST /events/refund-confirmations`：

```json
{"event_id":"refund-confirm-1","refund_no":"refund-1","expected_amount_fen":10000,"confirmed_at":"2026-10-28T14:00:00+08:00"}
```

切换到管理员授权，`POST /demo/clock/advance`：

```json
{"hours":72}
```

推进会自动检查，不需要再调用“检查”按钮。换回用户A查看问题及通知。`POST /events/refund-arrivals`：

```json
{"event_id":"arrival-event-1","refund_no":"refund-1","arrival_no":"arrival-1","amount_fen":6000,"arrived_at":"2026-10-31T14:00:00+08:00"}
```

差额为4000分。再输入不同arrival_no的4000分到账后验证解决。重放同一到账不会累计两次。到账可以先于申请数据送达；信息齐全前不会把缺失当0；足额判断优先，避免过时逾期消息。

### 3.4 暂停与关闭

`PUT /me/settings`：

```json
{"event_id":"pause-1","paused":true}
```

暂停仍保存新事实，不创建问题、不自动结案、不主动提醒；恢复使用新event_id、paused=false。恢复先汇总最新事实，不逐条补发暂停期间到期消息。

关闭问题后到账可展示，但关闭结论不自动改写、不重开。此前未知的外部操作仍可核实，关闭不是撤回。

### 3.5 模拟通知异常

管理员调用`PUT /demo/notification-behavior`，behavior支持：

- SUCCESS：明确成功，收件箱可见。
- FAIL：明确失败，不计成功，不自动重发。
- UNKNOWN_THEN_SUCCESS：外部可能已发、本地暂未知，下一轮按原operation_id核实。
- UNKNOWN_THEN_FAILURE：提交结果未知，下一轮查询确认失败。

设置仅影响后续提交，不改写已有操作。后台会查询未知操作，即使用户已暂停/关闭也只核实此前操作，不继续发送。模拟收件箱与业务任务状态分开，PENDING不算发送成功。

## 4. 自动测试与等价可重放演示

完整测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

最推荐的独立演示：

```powershell
.\.venv\Scripts\python.exe demo.py --managed-server --port 8001 --output reports/demo_output.txt
```

脚本启动自己的本地子进程，每场景使用新的独立SQLite文件，真实调用HTTP接口。场景10停止服务进程并以同一库重启，核对计数、状态、时钟和消息，不用进程内对象替代重启。

托管演示后台间隔设为3600秒，以避免未知状态被周期检查抢先改变；事件和推进仍自动检查，同一业务代码不变。正常服务默认每秒检查；主动后台机制由独立API/线程测试验证。

输出：

- `reports/demo_output.txt`：实际输入、时间推进、观察结果及断言。
- `reports/demo_output.json`：每场景PASS/FAIL/SKIP、断言、证据和数据库目录。
- `reports/demo_output.server.log`：服务端错误/管理审计等可取得日志。

演示数据库保留在报告列出的本机临时目录，便于事后分析，不自动删除原始现场；目录及SQLite/WAL/SHM文件应一起保留。它们都是虚构数据，不必加入源码仓库。

单场景：

```powershell
.\.venv\Scripts\python.exe demo.py --managed-server --scenario 4 --port 8001 --output reports/scenario4.txt
```

连接已有服务：

```powershell
.\.venv\Scripts\python.exe demo.py --base-url http://127.0.0.1:8000 --scenario 6 --output reports/scenario6.txt
```

外接服务会推进全局模拟时间，适合专用演示库；脚本只关闭自己创建的问题，不删除已有数据。外接方式不具备停止服务的所有权，场景10明确SKIP；SKIP或FAIL退出码为1，不算完整验收通过。

所有命令执行后以实际报告为准，本文不是通过证明。每场景都能通过scenario编号定位，具体验收结果见`reports/acceptance.md`及实际输出。可以录制5—10分钟讲解上述流程，但视频不能替代源码和运行说明。

## 5. 数据、设计和可追溯性

技术设计：`docs/superpowers/specs/2026-10-28-bill-followup-design.md`。
执行计划：`docs/superpowers/plans/2026-10-28-bill-followup-implementation.md`。

12张表分别保留用户、设置、时钟、每次原始输入、处理轨迹、交易、退款、到账、问题、关联、提醒和外部操作。原始数据是否影响业务都保留；事件级和业务编号级两层去重。事件原文不覆盖，处理结果可以更新但追加历史，能解释为什么未处理、重复、关闭后只补事实或发送结果未知。

本版使用UUID文本主键，属于小数据量实现取舍；外部operation_id独立于数据库记录id。持久化使用SQLite，不声称提供分布式部署或任意真实消息服务的跨系统exactly-once保证。

## 6. 复用、AI协作与限制

复用现有项目的基础目录和早期config/clock/database/models骨架；按已确认设计改造模型和业务。FastAPI、SQLAlchemy、Uvicorn、pytest、httpx、tzdata为现有免费组件；未复用真实支付或银行组件。新增业务规则、TDD测试、接口、后台调度、模拟通知、演示及材料属于本题改动，可由Git提交查看。

AI协作记录与人工贡献索引见`docs/ai-records/`。应交可取得的原始会话和工具结果，不以本README或总结替代；缺失范围如实说明，不提供隐藏推理或无关私人会话。

明确限制：本地测试Token不是生产登录；单进程/单worker；不自动纠正冲突事实，不对明确发送失败自动重试，不提供自然语言聊天解析和自定义前端。真实模式业务周期阈值仍为24/72小时，测试通过模拟时间加速。未完成或未验证项目以验收报告列明，不将设想写成已实现。

最终验收摘要：干净虚拟环境完整回归78项通过（1条第三方测试工具弃用警告）；HTTP重放10场景通过，包括真实进程重启。详见reports/pytest-final.txt、reports/acceptance.md。保存点式多进程竞争自动恢复未实现；仅支持已声明的单worker。豆包聊天由用户另行整理，Codex快照后的尾部范围已注明。

补充：用户已明确豆包完整聊天由其另行整理交付。本项目不再负责导出或补齐豆包聊天；doubao目录仅保留已经取得的七份分析/review参考材料，不冒充完整会话。当前导出范围为Codex主会话、基础/API/demo子任务及最终review任务。