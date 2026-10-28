# 账单持续跟进系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本项目采用当前会话顺序执行，避免并行修改同一模型；不额外开启子任务。

**Goal:** 在最多4小时约束内交付可运行的双用户账单跟进服务、Swagger、自动检查、可重放演示、验收证据与协作记录。

**Architecture:** Python同步FastAPI接口、同步SQLAlchemy、SQLite文件库，单worker及threading.Lock串行写入，独立后台线程检查。12张表，原始接收与处理历史独立留存；外部通知本地模拟并按operation_id核实。真实/模拟时钟配置切换，模拟推进自动触发检查。

**Tech Stack:** Python 3.12、FastAPI、Uvicorn、SQLAlchemy 2、Pydantic、pytest、httpx、tzdata。

**依据:** `docs/superpowers/specs/2026-10-28-bill-followup-design.md` V0.2，已获用户确认；现有代码仅基础骨架，当前虚拟环境缺SQLAlchemy。

---

## 1. 执行方式：一次写完计划，分阶段交付

不分批撰写计划、不为每小步停下来确认。按下述任务顺序做，每个任务完成后更新复选框、记录实际验证结果，并提交能独立review的改动。有失败先修该任务，不把失败带入后续功能；必要需求实现不了则明确记录缺口。

以下预算合计240分钟，是完整执行预算，不代表现在仍有240分钟。开始实施时记录实际时间和剩余额度；已消耗时间从总额扣除。最后至少30分钟优先保证演示、说明、记录和打包；不会因计划预算延长约定截止时间。

| 阶段 | 任务 | 完整预算 | 检查点 |
|---|---|---|---|
| 基础 | 1 配置/库/时钟 | 25分钟 | 干净库初始化、配置和时钟测试通过 |
| 输入 | 2 留存/隔离/幂等 | 30分钟 | 输入可还原，重复不重复生效 |
| 检测 | 3 重复、4 退款 | 45分钟 | 场景1—5通过 |
| 跟进 | 5 动作/任务、6 通知/恢复 | 70分钟 | 场景6—10及停止/未知边界通过 |
| 入口 | 7 Swagger/线程 | 30分钟 | HTTP可操作、后台无消息仍检查 |
| 交付 | 8 重放/回归/材料 | 40分钟 | 真实启动、重启证据和交付清单 |

测试、接口样例和README随实现同步积累，不全部拖到最后。自定义前端不列入本计划。

## 2. 文件与职责

| 路径 | 动作/职责 |
|---|---|
| requirements.txt、requirements-dev.txt、.env.example | 新建固定依赖与无真实秘密配置样例 |
| app/config.py、app/database.py、app/models.py、app/clock.py | 改造现有基础文件；12表、UTC毫秒、整数分、配置校验 |
| app/auth.py、app/schemas.py | 新建测试身份、管理员范围及输入输出定义 |
| app/services/__init__.py、app/services/events.py | 新建接收、规范身份、内容冲突、恢复状态 |
| app/services/issues.py | 新建检测、退款汇总、动作与历史 |
| app/services/reminders.py、app/notifications.py | 新建提醒和外部模拟提交/查询 |
| app/service.py | 薄应用服务入口，统一加锁/事务/调度，不复制领域逻辑 |
| app/scheduler.py、app/api.py、app/main.py | 后台线程、Swagger路由和生命周期 |
| tests/conftest.py | 独立文件库、可控时钟、服务fixture |
| tests/test_config_clock.py、tests/test_schema.py | 配置、表约束、时间持久化 |
| tests/test_events_auth.py | 原始接收、幂等冲突、用户边界 |
| tests/test_duplicates.py、tests/test_refunds.py | 场景1—5、阈值边界 |
| tests/test_actions_reminders.py、tests/test_notifications_recovery.py | 场景6—10及未知、关闭先后 |
| tests/test_api_scheduler.py | HTTP、认证、Swagger、后台线程和推进 |
| demo.py、tests/fixtures/scenarios.json | HTTP重放、虚构输入、断言 |
| README.md、reports/acceptance.md、reports/demo_output.txt | 运行说明及实际证据 |
| docs/ai-records/README.md、docs/ai-records/index.md | 原始AI记录取得方式、索引、缺失范围、人工贡献 |

## 3. 公共接口契约

为避免任务间接口漂移，先固定以下命名；实现可在对应模块内细分，不新造第二套入口。

```python
# app/config.py
load_settings(environ=None) -> Settings
# Settings: time_mode, timezone, check_interval_seconds, simulated_start,
# db_path, test_tokens, demo_admin_token, enable_demo_controls

# app/database.py
create_database(settings) -> Database
# Database.session_factory；initialize()校验schema/mode；dispose()关闭引擎

# app/clock.py
Clock.now_ms() -> int
# SimulatedClock.advance_to(target_ms: int) -> int：事务持久化，不倒退

# app/service.py
BillingService(database, clock, notification_gateway)
# 服务公开写入口统一取得同一个threading.Lock
receive(user_id: str, event_type: str, payload: dict) -> dict
act(user_id: str, issue_id: str, payload: dict) -> dict
set_paused(user_id: str, payload: dict) -> dict
get_issue(user_id: str, issue_id: str) -> dict
list_issues(user_id: str) -> list[dict]
get_refund(user_id: str, refund_no: str) -> dict
list_notifications(user_id: str) -> list[dict]
evaluate() -> None
recover() -> None
advance_clock(target_ms: int) -> int

# app/notifications.py
submit(operation_id: str, user_id: str, task_id: str, content: dict) -> dict
query(operation_id: str) -> dict
# 返回state=SENT/FAILED/UNKNOWN/NOT_SUBMITTED；SENT有delivered_at
```

服务fixture中service为BillingService，user-a/user-b为固定虚构用户。测试时间BASE=1793167200000（2026-10-28 14:00北京时间）；HOUR=3600000。所有测试使用自身新库，不能复用项目data中的演示库。

输入契约示例：

```python
txn = {
    'event_id': 'evt-t1', 'tx_no': 't1', 'merchant_id': 'shop-1',
    'amount_fen': 3000, 'occurred_at': '2026-10-28T09:00:00+08:00',
}
confirmation = {
    'event_id': 'evt-r1', 'refund_no': 'r1',
    'expected_amount_fen': 10000, 'confirmed_at': '2026-10-28T14:00:00+08:00',
}
arrival = {
    'event_id': 'evt-a1', 'refund_no': 'r1', 'arrival_no': 'a1',
    'amount_fen': 6000, 'arrived_at': '2026-10-31T14:00:00+08:00',
}
# receive类型：TXN / REFUND_CONFIRMED / REFUND_ARRIVAL
# act：event_id、action，SNOOZE另带tomorrow=true或remind_at完整时间
# get_issue：id、status、followup_mode、auto_followup_count、resolution_basis、
# remaining_amount_fen（非退款为null）、related_issue_ids、transactions、history
```

## Task 1：对齐依赖、12表、配置和持久化时钟（25分钟）

**文件：** requirements*.txt、.env.example、app/config.py、database.py、models.py、clock.py；tests/conftest.py、test_config_clock.py、test_schema.py。

- [ ] 固定本次实际安装并验证的依赖版本；运行 `.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt`。网络/写入被沙箱阻止时走正式权限升级，不绕过审批。
- [ ] 先写配置失败、模拟时钟重启保持、禁止倒退、两用户同编号、复合FK越权和活动提醒唯一约束测试；数据库使用tmp_path文件库。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_config_clock.py tests/test_schema.py -q`，确认失败源于未实现契约，而不是测试语法/导入路径错误。
- [ ] 按设计第4—5节一次建齐12张最小表与约束；业务UUID文本主键、整数分、UTC毫秒；原始输入与处理历史不可物理删除。启用外键、WAL、busy_timeout。
- [ ] 配置工厂不在import阶段创建真实数据库；显式初始化创建目录并验证schema_version/time_mode，错配拒绝启动；不删除旧库。真实/模拟默认分库，real禁止推进。
- [ ] 实现显式提交的时钟保存，确保不是仅flush；Session短生命周期。非法模式/负间隔/无时区时间拒绝。
- [ ] 重跑上述测试，预期全通过；`git diff --check`通过后提交 `feat: establish validated storage and clock foundations`。

最小测试模式：

```python
def test_invalid_mode_rejected():
    import pytest
    from app.config import load_settings
    with pytest.raises(ValueError):
        load_settings({'APP_TIME_MODE': 'typo'})

def test_clock_survives_database_reopen(database, clock, reopen_database):
    target = clock.now_ms() + 86400000
    clock.advance_to(target)
    database.dispose()
    reopened_clock = reopen_database().clock
    assert reopened_clock.now_ms() == target
```

reopen_database fixture返回拥有database/clock的新测试上下文，必须重新建引擎，不能返回旧对象。

## Task 2：原始接收、幂等、内容冲突及用户隔离（30分钟）

**文件：** app/services/events.py、app/auth.py、app/service.py、tests/test_events_auth.py。

- [ ] 写测试：同事件两次接收留下两条receipt但只有一份业务事实；换event_id重放同交易也不新增；同event_id不同内容冲突；两用户同tx_no允许；用户A不能读B资源。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_events_auth.py -q`，预期新测试失败。
- [ ] 实现原始receipt独立事务提交、规范身份部分唯一约束、规范哈希、同用户复合关联和追加processing_history。用户身份由调用方认证上下文提供，不信任payload.user_id。
- [ ] 唯一冲突用保存点回滚后按具体键重查，比较内容；FK/CHECK错误不能吞成重复。写入失败后保留receipt及FAILED尝试，不覆盖既有事实。
- [ ] 暂停时允许事实落盘、receipt标DEFERRED；不评估问题/发消息。恢复时先汇总全部事实，不逐个输入触发通知。
- [ ] 运行测试，预期通过；提交 `feat: retain inputs and enforce scoped idempotency`。

```python
def test_replay_retains_receipts_without_double_fact(service, txn_payload, query_rows):
    first = service.receive('user-a', 'TXN', txn_payload)
    second = service.receive('user-a', 'TXN', txn_payload)
    assert first['result'] == 'APPLIED'
    assert second['result'] == 'DUPLICATE'
    assert len(query_rows('event_receipts', user_id='user-a')) == 2
    assert len(query_rows('transactions', user_id='user-a')) == 1
```

query_rows仅测试fixture提供，按白名单表查询，不把此能力暴露为HTTP接口。

## Task 3：疑似重复检测及正常购买结论（20分钟）

**文件：** app/services/issues.py、app/service.py、tests/test_duplicates.py。

- [ ] 测试09:00/09:05形成一条；三笔09:00/09:05/09:09形成三条；09:00/09:05/09:11仅两条；10分钟整包含、超1ms不包含；不同用户不能配对。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_duplicates.py -q`，确认检测测试失败。
- [ ] 实现(user,merchant,amount,time±10min)候选逐对匹配，排序UUID内部id编码identity_key，数据库唯一约束兜底；每问题两条issue_transactions。
- [ ] 实现NORMAL动作及历史，重放交易不重开；get_issue展示原因、相关交易、共享交易关联问题。无cluster维护，不做连通分量推导。
- [ ] 测试全通过，提交 `feat: detect and retain duplicate transaction pairs`。

```python
def test_three_transactions_form_three_pairs(service, make_txn):
    for no, minute in [('a', 0), ('b', 5), ('c', 9)]:
        service.receive('user-a', 'TXN', make_txn(no, minute))
    issues = service.list_issues('user-a')
    assert len(issues) == 3
    assert all(x['status'] == 'OPEN' for x in issues)
    for issue in issues:
        assert len(service.get_issue('user-a', issue['id'])['related_issue_ids']) == 2
```

## Task 4：退款乱序、差额和验证解决（25分钟）

**文件：** app/services/issues.py、app/services/events.py、tests/test_refunds.py。

- [ ] 写测试：确认后72h前不逾期、满72h逾期；100退60剩40，再退40解决；重放到账不累计；全额先到后补申请不生成过时提醒；关闭后到账维持关闭。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_refunds.py -q`，预期退款行为测试失败。
- [ ] 实现退款空容器、申请确认补齐、arrival_no去重、SUM到账、差额和超额展示；先判断足额再逾期。交易未到也保留外部tx_no。
- [ ] 仅未暂停、非关闭退款问题自动FULL_REFUND_VERIFIED；重复问题绝不以退款到账自动解决。已用户解决只补证据，不覆盖原依据。
- [ ] 测试通过，提交 `feat: reconcile out-of-order refund facts`。

```python
def test_partial_then_full_refund(service, confirmation_payload, make_arrival):
    service.receive('user-a', 'REFUND_CONFIRMED', confirmation_payload)
    service.advance_clock(1793167200000 + 72 * 3600000)
    service.receive('user-a', 'REFUND_ARRIVAL', make_arrival('a1', 6000))
    assert service.get_refund('user-a', 'r1')['remaining_amount_fen'] == 4000
    second = make_arrival('a2', 4000)
    service.receive('user-a', 'REFUND_ARRIVAL', second)
    service.receive('user-a', 'REFUND_ARRIVAL', second)
    assert service.get_refund('user-a', 'r1')['received_amount_fen'] == 10000
    issue = service.list_issues('user-a')[0]
    assert issue['resolution_basis'] == 'FULL_REFUND_VERIFIED'
```

## Task 5：用户动作、提醒资格、延期及暂停关闭（35分钟）

**文件：** app/services/issues.py、app/services/reminders.py、app/service.py、tests/test_actions_reminders.py。

- [ ] 写测试：首次0次追加；不足24h不催；24/48h两次后停止；部分到账不重置；CONFIRM_PROBLEM仍待解决且停无回复催；延期不提前不清零。
- [ ] 写测试：次数耗尽后的用户约定允许一次、发完不恢复自动催；新约定取消旧PENDING但保留历史；缺日期时间拒绝；关闭重开保留计数；暂停跨多天恢复不批量补发。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_actions_reminders.py -q`，预期任务/动作测试失败。
- [ ] 实现status与followup_mode分离、generation/requested_remind_at、INITIAL/AUTO/USER任务键、一个活动任务的部分唯一索引。
- [ ] 每次发送前用最新退款事实和控制状态复检；自动追加从实际成功发送时间+24h算，不用循环补历史。失败任务不重建。
- [ ] 暂停保存事实不评估，恢复先汇总；关闭仅补事实；REOPEN显式触发并等待新约定，退款足额则按事实验证解决。
- [ ] 用注入的成功网关测试替身验证本任务，Task6再对接持久化模拟网关；重跑测试通过，提交 `feat: implement controlled follow-up scheduling`。

```python
def test_unanswered_followups_stop_after_two(service, open_duplicate):
    issue_id = open_duplicate()
    service.evaluate()
    assert service.get_issue('user-a', issue_id)['auto_followup_count'] == 0
    for hour, expected in [(23, 0), (24, 1), (48, 2), (96, 2)]:
        service.advance_clock(1793167200000 + hour * 3600000)
        assert service.get_issue('user-a', issue_id)['auto_followup_count'] == expected
    assert len(service.list_notifications('user-a')) == 3
```

## Task 6：模拟外部效果、未知核实与恢复（35分钟）

**文件：** app/notifications.py、app/services/reminders.py、app/service.py、tests/test_notifications_recovery.py。

- [ ] 写成功/失败/未知后成功/未知后失败测试；未知不重提、不增加计数，核实成功只增加一次；消息已发后关闭不撤回，关闭先接受则不提交。
- [ ] 写未知期间新增约定版本保留测试；SUBMITTING提交后未调用服务的崩溃窗口；服务执行后本地未确认窗口；文件库重新开引擎后recover保留计数和身份。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_notifications_recovery.py -q`，预期外部与恢复测试失败。
- [ ] 实现notification_operations持久化与同operation_id幂等；SENT行作为模拟收件箱，业务UNKNOWN与provider已SENT可以暂时不同。
- [ ] SUBMITTING先提交，再调用本地模拟服务，结果落业务事务；后台下一轮按同operation_id查询。明确失败不自动重试，NOT_SUBMITTED仅按本地模拟权威语义判未发生。
- [ ] 实现recover：读取库/时钟、恢复接收、核实旧操作、汇总事实、取消失效任务；不能恢复即盲发。旧USER成功只清相同generation，保留期间的新约定。
- [ ] 测试通过，提交 `feat: reconcile simulated notification outcomes and recovery`。

## Task 7：Swagger、权限、后台线程及健康状态（30分钟）

**文件：** app/main.py、api.py、schemas.py、auth.py、scheduler.py、tests/test_api_scheduler.py。

- [ ] 测试/docs及openapi.json可用；用户Token隔离，管理员推进不能冒充用户动作；无效输入仍保存认证后原始receipt；real推进拒绝。
- [ ] 测试后台无人发消息时仍周期调用evaluate；模拟推进立即检查；停止生命周期线程退出；后台异常显示在health且下轮可继续。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_api_scheduler.py -q`，确认入口/生命周期测试失败。
- [ ] 按设计第8节提供同步路由，复用service；认证后落原文再结构校验；JSON解析失败保存受限原文和错误；读取接口无推进副作用。
- [ ] lifespan同步管理数据库和后台线程；threading.Event.wait间隔；每轮独立Session；单worker启动；模式与schema错配直接失败。
- [ ] 运行 `.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1`，HTTP GET /health为200，/docs可操作。后台启动用隐藏窗口或工具会话，不弹出额外终端。
- [ ] 测试通过，提交 `feat: expose Swagger workflows and background checks`。

启动、配置、Swagger授权方法此时同步写README；不得到演示阶段才发现没有可运行入口。

## Task 8：重放演示、完整回归、交付材料（40分钟）

**文件：** demo.py、tests/fixtures/scenarios.json、README.md、reports/*、docs/ai-records/*。

- [ ] demo.py实现 `--base-url`、`--scenario all|1..10`、`--output`；HTTP调用真实接口，输出场景、输入、推进、观察结果及断言。失败退出非0，报告不伪造PASS。
- [ ] demo以唯一场景前缀生成事件/交易/退款编号并记录起始时钟，各场景使用相对时间和独立编号；用户设置在场景结束恢复。测试夹具独立库，合并HTTP演示不得让旧问题继续催办污染断言，可在场景结束显式关闭剩余问题并留痕。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest -q`，保留实际测试摘要；失败按场景修复并复测，不在报告写通过。
- [ ] 运行 `.\.venv\Scripts\python.exe demo.py --base-url http://127.0.0.1:8000 --scenario all --output reports/demo_output.txt`，预期10场景断言通过，产生实际证据。
- [ ] 停止真实服务、保持同一文件库并重新启动；核对指定问题状态/计数/模拟时间/已发消息，重放输入后不变。记录真实进程重启证据，与进程内测试区分。
- [ ] 新建一个干净临时环境按requirements安装和README启动，运行验收；若受网络/时间限制未完成，报告明确验证范围而非声称干净环境通过。
- [ ] README写依赖版本、配置、启动、Swagger授权、输入样例、推进时间、查询、pytest和demo命令、模拟范围、复用/改动、缺口和后台单worker限制。
- [ ] reports/acceptance.md逐场景记录输入/预期/实际/证据位置，异常修复及复测分开。截图/录屏不替代代码；可重放demo满足演示形式，时间够再录5—10分钟视频。
- [ ] AI原始记录：取得豆包与Codex会话/工具原文，保留失败修正；index列来源、范围、文件、关键人工判断和个人贡献。不可导出部分写缺失范围并交可得原文，不用总结冒充；不额外收集无关私人会话或密钥。
- [ ] `git diff --check`、检查暂存文件不含.env/Token秘密/真实数据；提交 `docs: deliver reproducible acceptance and collaboration records`。本地提交不等于BOSS提交，最终给用户交付路径及准确结果，不擅自发送外部消息。

## 4. 覆盖核对与完成判定

| 需求 | 实施任务 |
|---|---|
| 无序交易对、三笔关联、正常重放 | 2、3 |
| 退款72h、分笔/乱序/去重、足额结案 | 2、4 |
| 正常/确认问题/解决/关闭分离 | 3、5 |
| 24h两次追加、延期不清零、主动约定 | 5、6 |
| 暂停恢复、关闭重开、发送先后 | 5、6、7 |
| 未知不盲重试、成功只计一次 | 6 |
| 原始接收和处理轨迹、内容冲突 | 2贯穿3—7 |
| 多用户数据/设置/权限隔离 | 1、2、7 |
| 主动检查、双时钟和中断恢复 | 1、6、7、8 |
| 可运行、演示、证据、AI记录 | 7、8 |

完成必须有实际验证命令和结果，不能依据写完代码勾通过。任务清单执行中勾选，失败/未实现保留未勾状态并写缺口。

## 5. 风险与效率约束

- 不一次性编写所有业务代码再测试；每任务以行为测试为入口，测试自身错误先纠正。
- 不为低影响文档/样式改动写镜像测试；对幂等、状态、时间、权限、恢复写有意义测试。
- 不引入Redis、Celery、真实支付、LLM运行依赖、自定义前端和分布式ID服务。
- 精简字段/文件不能删原始输入或未知核实证据；不得用“10场景全绿”替代其他明确需求。
- 每个检查点给简短进度和剩余风险，不因常规选择重复问用户；有真正需求冲突或权限限制再说明。
- 任务预算超出剩余时长时先锁定可运行成果和交付材料，未完成项如实标缺口，不延长4小时。
## 6. 实际执行回填（2026-10-28）

| 任务 | 实际状态/证据 |
|---|---|
| 1 基础 | 完成；tests/test_foundations.py合并配置、schema、clock测试，12项通过；e95d5c0 |
| 2 输入 | 完成原始留存、规范身份、内容冲突及用户隔离；保存点自动竞争恢复未实现，失败回滚留痕 |
| 3 重复 | 完成；业务测试和HTTP场景1/2/3验证 |
| 4 退款 | 完成；业务测试和HTTP场景4/5/9验证 |
| 5 动作提醒 | 完成；场景6/7/8、暂停恢复、次数耗尽后主动约定等验证 |
| 6 通知恢复 | 完成最小成功/失败/未知查询模型；业务测试与真实重启场景10验证 |
| 7 HTTP后台 | 完成；Swagger、权限、审计及HTTP异常测试，两个HTTP测试文件共19项 |
| 8 材料 | demo十场景通过；干净环境78项通过；协作记录仅取得范围已交，豆包完整聊天缺失 |

实施为节省时间将issues/reminders业务集中在app/service.py；events规范化独立于services/events.py，网关独立notifications.py。测试合并到test_foundations.py和test_business.py；演示数据由demo与测试函数生成，不再额外维护静态scenarios.json。接口与文档随实施同步完成。

原计划逐步复选框保留为最初步骤，不据此虚构执行过不存在的文件命令；实际路径和证据以本节及验收报告为准。已实现与缺口明确分开，不将所有原计划内部增强项宣称完成。
## 7. 后续授权：简单单页（追加阶段）

用户确认先前后端验收后，授权约半小时补充HTML/CSS/原生JavaScript页面，并豁免前端TDD；后端静态托管仍先RED后GREEN。新增app/static三文件及scripts/verify_page.py，修改main挂载入口；业务服务不变。页面覆盖问题、操作、历史、模拟数据和全局时间控制，使用原有鉴权接口。完整80项回归及真实浏览器11项验证通过，证据见reports。无需外网前端资源，无独立构建流程，不引入运行时Playwright依赖。