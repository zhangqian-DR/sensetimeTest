# 账单问题持续跟进系统技术设计（修订版 V0.2）

- 日期：2026-10-28；状态：目标设计，待审阅。本文不代表代码已实现或验收已通过。
- 约束：北京时间14:00—18:00，最多4小时，到上限交当前成果及缺口。
- 技术：Python、FastAPI、同步SQLAlchemy、SQLite、pytest；第一版Swagger操作，HTTP脚本重放演示；自定义前端后置。
- 范围：疑似重复消费和退款逾期；用户、账目、外部通知全部模拟，不操作真实账户，不删除交易、不联系商户。

## 1. 本次修订与设计取舍

1. 确定同步路线：同步def接口、同步数据库Session、threading.Lock、单后台线程、单worker；不混入一套异步数据库栈。
2. 保留应用生成UUID文本主键。此为考试小数据量场景的实现取舍，外部operation_id独立生成，不将它与数据库主键用途混淆；不声称UUID不适合所有生产环境。
3. 持久化模型从14张表精简至12张：accepted_events并入event_receipts的规范接收状态；notification_messages并入notification_operations的投递事实。原始输入和追加处理历史继续分别保存。
4. 不建设商户管理、cluster分组或复杂故障编排。共享交易直接展示关联问题；模拟通知采用成功、失败、未知后核实的最小能力。
5. 重新初始化引擎验证与真实进程重启验证明确区分，不把前者称为后者。
6. 精简结构，不削减持久化、用户隔离、幂等、未知结果核实、停止控制和现场还原能力。

## 2. 已确认的业务规则

### 2.1 问题检测与结案

- 重复消费：同用户、同商户编号、同额、不同交易编号，时间差≤10分钟。每个无序交易对仅一条问题，原交易保留。
- 三笔两两满足创建AB、AC、BC三条；09:00/09:05/09:11仅AB、BC两条。共享交易只能用于展示关联，不能推导不满足时间条件的新交易对。
- 统一称“疑似重复消费”，不得称已证实错误扣款。
- 退款按用户及退款编号归集。申请确认满72小时且未足额到账才创建逾期问题并提醒。到账可先于申请送达，信息不足时等待，不把缺失金额当0。
- 正常购买、确认有问题、用户确认解决、关闭分别记录。确认有问题不等于解决。
- 重复问题只能凭用户明确确认解决结案，依据USER_CONFIRMED；退款逾期可以凭足额到账验证解决，依据FULL_REFUND_VERIFIED。
- 部分到账显示差额，足额到账优先于逾期检查，避免发出已过时的消息。正常、解决、关闭问题不会因输入重放重新打开。

### 2.2 提醒和用户控制

- 首次成功提醒不计追加；未回复时，距最近成功提醒至少24小时自动追加，最多两次。
- 部分到账、延期、暂停恢复、重启、关闭重开都不清零计数。
- 确认有问题后退出无回复催办，等待新事实或下一项明确约定。
- 用户主动约定独立于自动追加次数。次数耗尽仍允许明确约定一次；发送后等待事实/下一次约定，不恢复自动催办。
- 明天提醒为max(既有最早提醒约束, now+24小时)。指定日期必须提供时间及时区，否则返回澄清提示，不默认猜测。
- 暂停：继续接收、落盘事实，标记待评估；不新建问题、不自动结案、不发送。恢复时先处理最新事实，再决定当前下一步，不逐条补过期提醒。
- 关闭：继续保存事实并展示到账变化，不自动转换关闭结论、不发新消息。重新开启必须用户明确要求；先核对最新事实，仍未解决则等新约定，不立即补旧任务。
- 已发消息保留；已提交未知操作允许只读核实。暂停/关闭不等于撤回此前操作。

### 2.3 留存与诚实性

每次业务输入都保留，重复也保留；每次处理尝试追加历史。是否接收、是否生效、是否发送成功分开表示。失败和未知不伪装为0、成功或完成。交易备注等外部文字只作数据，不能授权、变更控制规则或读取他人数据。

## 3. 架构、执行路线和配置

### 3.1 单进程同步服务

- FastAPI同步def业务接口，由线程池执行。SQLAlchemy采用同步引擎，每个请求/后台检查单独创建和关闭Session，不跨线程共享Session。
- 单进程threading.Lock串行化修改、时间推进、调度和本地模拟提交。锁范围覆盖提交前复检及本地模拟调用，确保关闭与发送有确定先后。
- 后台线程用Event.wait(interval)周期检查；真实模式无人操作也会推进。FastAPI生命周期负责启动和有界停止，不用阻塞sleep占住请求线程。
- 启动只允许一个worker；本版不支持多个进程共享同一业务库运行调度。数据库约束仍兜底，但不把进程锁宣称为分布式锁。
- 服务层管理事务与副作用；纯规则函数只计算。HTTP、后台和demo共用同一业务实现。
- 避免嵌套获取同一Lock：锁由最外层服务入口持有，内部方法不再次加锁；先加业务锁，再开始写事务，固定顺序。

### 3.2 建议代码边界

| 文件/模块 | 职责 |
|---|---|
| config.py / clock.py | 配置校验、UTC毫秒时间、双时钟 |
| database.py / models.py | 引擎、独立Session、事务和约束 |
| schemas.py / auth.py | 结构化输入、测试Token身份和权限 |
| services/events.py | 接收日志、规范事件、去重冲突及恢复 |
| services/issues.py | 两类检测、用户动作、事实汇总及处理历史 |
| services/reminders.py | 资格、任务、成功计数、取消与未知核实 |
| notifications.py / scheduler.py | 本地模拟网关、后台周期检查 |
| main.py / api.py | 应用生命周期和Swagger接口 |
| demo.py / tests/ | HTTP重放、业务测试及恢复验证 |

这些是职责边界，可按代码规模合并文件，不为凑模块拆空层。

### 3.3 配置示例

```dotenv
APP_TIME_MODE=simulated
APP_TIMEZONE=Asia/Shanghai
APP_CHECK_INTERVAL_SECONDS=1
APP_SIMULATED_START=2026-10-28T14:00:00+08:00
APP_DB_PATH=./data/simulated.db
APP_TEST_TOKENS_JSON={"demo-token-a":"user-a","demo-token-b":"user-b"}
APP_DEMO_ADMIN_TOKEN=local-demo-admin-only
APP_ENABLE_DEMO_CONTROLS=true
```

- mode仅real/simulated，无效模式、非正间隔、无效时区/时间启动失败。timezone固定Asia/Shanghai，本版拒绝配置其他展示时区。
- 配置启动时读取，切换重启生效。real默认data/real.db，simulated默认data/simulated.db；库内保存mode，错配拒绝启动。
- real真实走时，禁止推进接口。simulated时间不自行流逝，推进接口落盘后自动检查；模拟初始时间仅首次使用，重启读取保存值，禁止倒退。
- 后台查询未知操作按检查轮次执行，不依赖模拟时钟自然流逝，避免未知操作在固定虚拟时间下永远无法核实。
- API时间必须ISO 8601带时区，入库UTC毫秒整数；响应转北京时间+08:00。输入精度限毫秒，超过精度返回校验提示。Windows依赖包含tzdata。
- 金额整数分，字段名amount_fen/expected_amount_fen，拒绝负数、浮点分和布尔；交易及到账金额>0。固定CNY。
- 10分钟、72小时、24小时、两次追加为固定业务规则；加速靠推进时钟，不修改规则。
- 用户由Bearer测试Token解析；不能由正文指定身份。普通用户只能访问自己的资源，跨用户统一404，无效Token为401。
- 全局推进时间及故障控制仅演示管理员可用，且必须simulated和开启演示控制。用户Token不具有管理权限；管理员控制不能冒充用户授权关闭问题。
- 所有Token仅本地演示；日志不记录Token、Authorization或真实密钥。示例Token不连接任何真实服务。

## 4. 数据模型总览与通用约束

共12张表：app_users、user_settings、app_meta、event_receipts、processing_history、transactions、refunds、refund_arrivals、issues、issue_transactions、reminder_tasks、notification_operations。

- 除用户、设置、元数据及关系表的自然/复合主键外，id采用uuid4生成的UUID TEXT。notification_operations用独立UUID operation_id作为PK。
- UUID用于本版生成简单、无外部ID服务依赖，不以发送前预分配为所有表使用UUID的必要理由。生产可按规模评估整数/有序UUID/雪花ID等，本文不实现分布式ID服务。
- *_at INTEGER为UTC毫秒；*_json TEXT为JSON；金额INTEGER分；布尔CHECK IN(0,1)。枚举用TEXT+CHECK限制取值，必填列NOT NULL。
- 用户业务表包含user_id；跨用户表关系用(user_id,id)复合FK，目标提供UNIQUE(user_id,id)。UUID本身不作为授权依据。
- 启用foreign_keys=ON、WAL和busy_timeout。索引按查询需要建立，不为所有字段单独建索引。
- 不物理删除事实或历史。原始payload、哈希和接收时间不可修改；处理状态可以更新，但每次变化必须追加processing_history。
- 用户修改请求必填event_id；所有返回区分接收、已应用、待处理、重复、冲突、失败。

## 5. 详细表结构

### 5.1 app_users / user_settings / app_meta

| 表 | 字段与约束 |
|---|---|
| app_users | id TEXT PK；display_name TEXT NOT NULL；created_at INTEGER NOT NULL |
| user_settings | user_id TEXT PK FK users；paused INTEGER NOT NULL DEFAULT 0；paused_at/resumed_at可空；updated_at NOT NULL |
| app_meta | key TEXT PK；value TEXT NOT NULL；updated_at INTEGER NOT NULL |

元数据保存schema_version、time_mode、virtual_now；Token来自配置，不存数据库。设置变更也保存接收和处理记录。

### 5.2 event_receipts：原始接收与规范事件身份

| 字段 | 类型/约束 | 说明 |
|---|---|---|
| id | TEXT PK | 每次输入独立生成，重放也新增 |
| user_id | TEXT NOT NULL FK | 已认证业务归属 |
| event_id | TEXT可空 | 输入稳定事件号，缺失时仍留原文 |
| event_type / source | TEXT NOT NULL | 交易/申请确认/到账/用户动作/设置/通知结果；API/BACKGROUND/SIMULATOR |
| payload_json | TEXT NOT NULL | 去除认证秘密后的原始业务输入 |
| payload_hash | TEXT NOT NULL | 按事件类型规范化内容的哈希，不含接收时间及请求追踪号 |
| occurred_at | INTEGER可空 | 事实发生时间，缺失/不合法不伪造 |
| received_at | INTEGER NOT NULL | 接收时间 |
| trace_id | TEXT NOT NULL | 请求和处理关联 |
| is_canonical | INTEGER NOT NULL DEFAULT 0 | 是否占有该用户的event_id幂等身份 |
| canonical_receipt_id | TEXT可空 | 重放/冲突指向首次规范记录；规范记录自身可空 |
| processing_state | TEXT NOT NULL | RECEIVED / ACCEPTED / DEFERRED / APPLIED / REJECTED / DUPLICATE / CONFLICT |
| applied_at | INTEGER可空 | 首次事实应用时间 |

约束与索引：UNIQUE(user_id,id)；部分唯一索引(user_id,event_id) WHERE is_canonical=1；CHECK规范记录必须有event_id；重复/冲突引用用同用户复合FK；索引(user_id,received_at)、(processing_state,received_at)。

处理规则：先独立提交一条is_canonical=0的原始记录。合法输入在业务锁内竞争规范身份；同event_id同内容指向首条并标DUPLICATE，不重复生效；同event_id不同内容标CONFLICT，首条内容不变。校验失败保留为REJECTED但不占用规范身份，允许修正请求后重新提交。

规范记录保存待恢复状态，替代独立accepted_events表。原始输入不变，状态变化由历史补充。处理崩溃后扫描RECEIVED和规范ACCEPTED/DEFERRED恢复；重复记录不重复执行，但应确保其规范记录仍在恢复队列。

同步校验中间件在认证后保存业务原文，再做结构校验，避免FastAPI直接422而没有接收证据。请求体不能决定认证归属；未认证请求只写无凭证安全摘要，不按任意user_id入业务表。无法解析JSON时保存受限大小的原始文本及解析错误，不伪造正常JSON事实。

### 5.3 processing_history：追加的处理、动作和状态变化

字段：id TEXT PK；user_id NOT NULL；receipt_id可空；issue_id可空；task_id可空；trigger TEXT NOT NULL；result TEXT NOT NULL；reason_code TEXT NOT NULL；detail_json TEXT NOT NULL；before_json/after_json可空；processed_at INTEGER NOT NULL。

关系均采用同用户复合FK。索引(user_id,issue_id,processed_at)、(user_id,receipt_id)、(user_id,task_id)。设置等用户级历史允许issue_id为空，仍可按用户和receipt查询。

记录APPLIED、DUPLICATE、CONFLICT、INVALID、DEFERRED_PAUSED、FACT_ONLY_CLOSED、TASK_CREATED、SENT、UNKNOWN、FAILED、CANCELLED等处理结果。每次尝试追加，不覆盖历史；记录操作者、触发源、事件引用和结案依据。外部提交响应和查询响应也保存到事件/历史，足够解释为何未知、何时核实。

### 5.4 transactions：交易事实

字段：id PK；user_id NOT NULL；tx_no TEXT NOT NULL；merchant_id TEXT NOT NULL；merchant_name可空；amount_fen INTEGER NOT NULL CHECK>0；currency TEXT NOT NULL CHECK='CNY'；occurred_at INTEGER NOT NULL；source_receipt_id NOT NULL；created_at INTEGER NOT NULL。

UNIQUE(user_id,tx_no)、UNIQUE(user_id,id)；复合FK引用规范receipt。查询索引(user_id,merchant_id,amount_fen,occurred_at)。按商户编号精确判断，不猜测名称相似度，不建设独立商户表。

### 5.5 refunds：退款容器

字段：id PK；user_id NOT NULL；refund_no TEXT NOT NULL；tx_no可空；expected_amount_fen INTEGER可空；confirmed_at INTEGER可空；confirmation_receipt_id可空；created_at/updated_at NOT NULL。

UNIQUE(user_id,refund_no)、UNIQUE(user_id,id)。CHECK应退为空或>0；确认时间和应退金额同时为空或同时非空。

到账先来：创建只有user/refund_no的容器；申请确认后补完整字段。缺失金额不当0，不计算逾期和足额。tx_no允许对应交易尚未到达，按(user_id,tx_no)延迟解析，不因交易缺失拒收退款。

同退款编号的申请确认重放不覆盖；金额、确认时间冲突时保留输入及异常，待人工分析。本版不实现自动纠正已确认退款信息。

### 5.6 refund_arrivals：到账事实

字段：id PK；user_id NOT NULL；refund_id NOT NULL；arrival_no TEXT NOT NULL；amount_fen INTEGER NOT NULL CHECK>0；arrived_at INTEGER NOT NULL；source_receipt_id NOT NULL；created_at INTEGER NOT NULL。

UNIQUE(user_id,arrival_no)、UNIQUE(user_id,id)；复合FK引用同用户退款和规范receipt。索引(user_id,refund_id)。arrival_no定义为用户范围稳定流水号，不能用金额+时间猜唯一性，以免误吞合法分笔到账。

累计到账SUM唯一到账行，不盲目累加余额。差额max(expected−sum,0)，超额单独显示并记录异常，不删事实，不执行额外资金操作。sum≥expected表示已足额，展示精确到账总额。

### 5.7 issues：问题结论和跟进控制

| 字段 | 类型/约束 | 说明 |
|---|---|---|
| id / user_id | TEXT PK / TEXT NOT NULL | 问题及归属 |
| issue_type | TEXT NOT NULL | DUPLICATE / REFUND_OVERDUE |
| identity_key | TEXT NOT NULL | 交易无序对或退款id |
| status | TEXT NOT NULL | OPEN / CONFIRMED_PROBLEM / NORMAL / RESOLVED / CLOSED |
| followup_mode | TEXT NOT NULL | AWAITING_REPLY / WAITING_FACT / USER_SCHEDULED / STOPPED |
| reason | TEXT NOT NULL | 检测依据，不能声称证实重复扣款 |
| refund_id | TEXT可空 | 仅退款问题使用，同用户FK |
| auto_followup_count | INTEGER NOT NULL DEFAULT 0 CHECK 0..2 | 成功自动追加次数 |
| initial_sent_at | INTEGER可空 | 首次成功提醒的发送时间 |
| last_sent_at | INTEGER可空 | 最近已确认成功提醒的发送时间 |
| not_before_at | INTEGER可空 | 当前最早允许提醒时刻 |
| schedule_generation | INTEGER NOT NULL DEFAULT 0 | 用户新约定版本，递增不清零 |
| requested_remind_at | INTEGER可空 | 当前用户明确约定，含未知操作期间接受的新约定 |
| resolution_basis | TEXT可空 | USER_CONFIRMED / FULL_REFUND_VERIFIED |
| resolution_detail_json | TEXT可空 | 用户动作或到账事实引用 |
| resolved_at / closed_at | INTEGER可空 | 分开保存 |
| created_at / updated_at | INTEGER NOT NULL | 时间 |

UNIQUE(user_id,issue_type,identity_key)、UNIQUE(user_id,id)；索引(user_id,status)、(followup_mode,not_before_at)。identity_key用排序两个交易id的JSON编码，退款用refund_id。新事实改变退款差额，不改变用户处理结论。

约束：RESOLVED必须有依据及resolved_at；DUPLICATE不能使用FULL_REFUND_VERIFIED。NORMAL只对重复类有效；NORMAL/RESOLVED/CLOSED的followup_mode=STOPPED。关闭已解决问题可保留此前解决依据，关闭不是新增解决证据。

### 5.8 issue_transactions：问题交易关系

user_id NOT NULL；issue_id NOT NULL；transaction_id NOT NULL；PK(issue_id,transaction_id)。分别用(user_id,issue_id)、(user_id,transaction_id)复合FK关联issues和transactions；索引(user_id,transaction_id)。

重复问题同一事务插入恰好两条关联。GET详情提供related_issue_ids及shared_transaction_ids；AB/AC/BC可直接互相关联。不要求连通分量算法，不以关联组代替逐对检测。退款通过refund.tx_no展示交易，缺失明确提示。

### 5.9 reminder_tasks：任务、提交状态和核实

| 字段 | 类型/约束 | 说明 |
|---|---|---|
| id / user_id / issue_id | TEXT | 同用户复合关联 |
| kind | TEXT NOT NULL | INITIAL / AUTO_FOLLOWUP / USER_SCHEDULED |
| task_key | TEXT NOT NULL | INITIAL、AUTO:1、AUTO:2、USER:版本号 |
| due_at | INTEGER NOT NULL | 计划发送下限 |
| status | TEXT NOT NULL | PENDING / SUBMITTING / SENT / FAILED / UNKNOWN / CANCELLED |
| operation_id | TEXT NOT NULL UNIQUE | 创建时独立分配，外部提交/查询使用 |
| content_json | TEXT可空 | 真正提交前按最新事实生成，随后冻结 |
| submitted_at / sent_at | INTEGER可空 | 提交时间/外部确认的实际发送时间 |
| confirmed_at | INTEGER可空 | 本地获知成功的时间，与发送时间区别 |
| counted_at | INTEGER可空 | 成功计数已生效，防重复查询重复增加 |
| source_receipt_id | TEXT可空 | 约定来源 |
| error_code / error_detail | TEXT可空 | 失败或未知说明 |
| created_at / updated_at | INTEGER NOT NULL | 时间 |

UNIQUE(user_id,issue_id,task_key)、UNIQUE(user_id,id)。索引(status,due_at)。部分唯一索引(user_id,issue_id) WHERE status IN ('PENDING','SUBMITTING','UNKNOWN')，限制每问题一个活动任务；启动验证SQLite支持所用特性。

再次延期时，尚未提交的旧任务标CANCELLED并创建新generation任务，保留旧安排，不多发。状态转换在同一事务完成，先取消后插入以满足唯一约束。已提交/未知不能取消外部效果：只保存新的requested_remind_at，等旧操作核实后安排，绝不并行重复提交。

失败不自动重试；相同task_key的FAILED不能被调度器重建。若用户重新约定可创建新USER版本，失败历史保留。核实确认发送成功时，同一事务条件更新counted_at：INITIAL设置initial_sent_at；AUTO增加一次count；USER不改count；更新last_sent_at和历史。重复成功响应不再计数。

### 5.10 notification_operations：模拟外部服务及收件箱

字段：operation_id TEXT PK；user_id NOT NULL；task_id TEXT NOT NULL UNIQUE；behavior TEXT NOT NULL；provider_state TEXT NOT NULL；recipient TEXT NOT NULL；content_json TEXT NOT NULL；submitted_at INTEGER NOT NULL；delivered_at INTEGER可空；error_detail TEXT可空。

behavior=SUCCESS/FAIL/UNKNOWN_THEN_SUCCESS/UNKNOWN_THEN_FAILURE。provider_state=PENDING/SENT/FAILED。使用稳定operation_id模拟提交幂等，任务关联同用户；与业务任务独立事务落盘。

- SUCCESS：提交产生SENT及delivered_at，返回成功。
- FAIL：提交产生FAILED，返回明确失败，不投递。
- UNKNOWN_THEN_SUCCESS：提交已产生SENT及delivered_at，但响应未知；下一次查询返回持久化成功事实。
- UNKNOWN_THEN_FAILURE：提交结果暂不可知，持久化PENDING；下一次查询揭示FAILED，无消息产生。

无需query_count或复杂编排。收件箱查询provider_state=SENT的行，显示实际内容与时间；不新增消息表。业务任务UNKNOWN与服务商SENT可以暂时不同，正是结果未知场景。

查询结果也记事件和历史；未找到operation只在本地模拟器的权威存储语义下判定未提交，业务标明确失败，不自动提交。真实服务若不能保证“查无即未发生”，应继续UNKNOWN，本版不假设真实平台具有此能力。

## 6. 接收、幂等、事务与异常

### 6.1 两层去重

事件层：规范receipt的(user_id,event_id)部分唯一约束；业务层：交易编号、退款编号、到账流水、问题身份、任务键各自唯一。规范哈希包括事件类型和全部业务内容，时间规范化UTC，金额用整数；同event_id包装不同类型视为冲突。

业务编号相同且内容一致：保留本次receipt并记录业务重复，不重复记账；内容不一致：CONFLICT，不覆盖首个事实。不同用户同编号允许，互不冲突。

### 6.2 写入流程与暂停

1. 认证后保存原始接收，独立事务提交。
2. 结构校验、取得业务锁，认领规范身份；业务状态与处理历史同事务提交。
3. 输入事实写入transactions/refunds/refund_arrivals；若暂停，receipt=DEFERRED，仅保存事实，不触发检测、问题结案和提醒。
4. 未暂停则汇总该用户最新事实、转换允许的状态、调整任务、追加历史；规范receipt改APPLIED。
5. 恢复时先汇总全部已保存事实，再创建/更新问题及任务；禁止按历史输入逐条触发通知，否则会误发后来已到账的逾期消息。

读取暂停中问题时明确“新事实待评估”，可展示已保存到账事实，不能把旧结论冒充最新检查结果。暂停期间接受用户显式关闭/修改约定等控制动作，但不主动发送；这些是用户指令，不是后台继续推进。

### 6.3 IntegrityError与失败

- 可预期唯一冲突：使用保存点隔离插入失败，回滚到保存点后按完整唯一键重查；内容一致按重复，内容不同按冲突。
- 不将所有IntegrityError吞成重复：FK、CHECK等违规记录具体错误并失败，不输出成功。
- 业务提交失败回滚状态、任务和历史同一事务；原始receipt仍在，另开可用事务记录FAILED处理尝试，保留待恢复状态。
- 数据库本身不可用：503并明确未接受/未完成，保留可取得的进程错误日志，不声称数据已保存。
- 原始输入与历史不含认证秘密；所有业务异常保留输入引用，不能用0或空成功掩盖失败。

## 7. 状态、调度、关闭与恢复

### 7.1 用户动作

| 动作 | 状态与后续 |
|---|---|
| NORMAL | 仅重复类；NORMAL/STOPPED；取消PENDING |
| CONFIRM_PROBLEM | CONFIRMED_PROBLEM/WAITING_FACT；取消无回复待催任务 |
| SNOOZE | 保留OPEN或CONFIRMED_PROBLEM；USER_SCHEDULED；保存约定并安排一条 |
| RESOLVE | RESOLVED/STOPPED；USER_CONFIRMED；保存原始动作引用 |
| CLOSE | CLOSED/STOPPED；取消PENDING，保留已提交、未知、已发记录 |
| REOPEN | 仅CLOSED显式开启；保留计数，退款已足额则验证解决，否则OPEN或恢复确认结论并WAITING_FACT |

REOPEN从历史恢复关闭前OPEN/CONFIRMED_PROBLEM的结论；原来已解决的仍保留解决事实，不靠重开抹去依据。NORMAL和RESOLVED不自动重开。本版不提供撤销正常/解决结论的隐式功能，重复动作返回无变化并留记录。

### 7.2 自动调度

- 首次：问题允许、未暂停、AWAITING_REPLY、无活动任务和既有失败首次任务，创建INITIAL。
- 自动追加：首次已成功、AWAITING_REPLY、count<2，due=max(last_sent_at+24h,not_before_at)；最多两次，次数只在确认成功后增加。
- 用户约定：使用requested_remind_at与现有约束较晚值；发送后WAITING_FACT，不恢复自动催办。重复/冲突用户动作不新增版本。USER任务携带其generation；确认旧任务结果时，仅在版本仍相同才清除requested_remind_at并转WAITING_FACT。若期间已有更新约定，保留新版本并在旧任务核实后安排；未知期间的新约定不丢失。
- 发送前重新读取用户暂停、问题结论和退款事实；足额、正常、关闭、解决则取消未提交任务。消息内容以此时事实生成，不沿用过时差额。
- 大幅推进或恢复：每问题一次检查最多实际发送一条，后续至少距最近实际成功发送24小时；不循环补0/24/48小时历史消息。
- 未知核实成功后，last_sent_at用提供者实际delivered_at；confirmed_at记核实时间。当前检查只核实，不连发下一条；下一轮才按资格决定。

### 7.3 关闭先后和提交窗口

业务锁串行控制修改和本地模拟发送。SUBMITTING先提交事务，再使用预分配operation_id调用本地模拟服务，最后落成功/失败/未知结果。

关闭事务先成功：后续发送复检必须停止。操作先提交：关闭不能声称撤回；UNKNOWN/SUBMITTING只查询已有operation_id，不重提。关闭/暂停后只读核实结果可更新真实发送计数和历史，但不得创建后续消息。

崩溃窗口：SUBMITTING已提交而服务未调用，启动查询模拟服务确认为不存在后标明确失败；服务已执行而业务未记结果，启动查询得到既有结果并只计一次。不存在不能触发盲重试。

### 7.4 启动与暂停恢复

读取schema/mode/时钟 → 恢复未完成接收及规范身份 → 查询SUBMITTING/UNKNOWN → 未暂停用户先汇总最新事实 → 更新非关闭问题 → 取消失效任务 → 安排当前合资格任务。暂停用户只保存事实及核实已提交操作，不业务推进。

关闭后足额退款：资金事实显示足额，status仍CLOSED，历史写FACT_ONLY_CLOSED；显式重新开启后才可依据最新事实验证解决。已用户确认解决的退款再足额到账只补证据，不覆盖原结案依据。

本地幂等模拟可验证单次外部效果，不承诺替换任意真实服务后自动实现跨系统exactly-once。

## 8. Swagger接口

| 接口 | 用途 |
|---|---|
| GET /health | 模式、版本、后台检查健康及最近错误，不泄露配置密钥 |
| GET /me | 当前虚构用户 |
| POST /events/transactions | event_id、tx_no、merchant_id、amount_fen、occurred_at |
| POST /events/refund-confirmations | event_id、refund_no、expected_amount_fen、confirmed_at、可选tx_no |
| POST /events/refund-arrivals | event_id、refund_no、arrival_no、amount_fen、arrived_at |
| GET /issues / GET /issues/{id} | 原因、关联交易/问题、资金事实、结论和依据 |
| GET /issues/{id}/history | 用户处理、输入引用、状态与提醒历史 |
| POST /issues/{id}/actions | event_id、枚举动作、条件提醒时间 |
| PUT /me/settings | event_id、paused；恢复自动触发汇总 |
| GET /refunds | 信息未齐、未逾期、部分/足额到账跟踪 |
| GET /reminders / GET /notifications | 任务及外部模拟收件箱 |
| GET /events/{receipt_id} | 当前用户原始输入和处理记录 |
| GET /demo/clock / POST /demo/clock/advance | 演示管理员；推进自动检查 |
| PUT /demo/notification-behavior | 演示管理员；设置后续结果，不影响已提交操作 |

日期缺时间/时区返回422及明确补充要求，不生成约定；不存在或跨用户资源404。用户不能直接改次数、结案依据、provider状态。GET不触发检测或发送。后台错误可观察，不静默吞异常。

全局演示控制记录管理员操作和前后时间到应用审计日志；不混入普通用户的授权事件。保留demo管理日志作为验收证据，不记录管理Token。

## 9. 验收、证据和恢复测试

所有场景独立数据库/时钟，合并演示时明确隔离。pytest覆盖规则与服务，demo.py通过HTTP输入事件、动作及推进时间，不直接写最终状态。对后台任务用有上限轮询等待结果，不能靠无限等待。

| 场景 | 必须检查的实际结果 |
|---|---|
| 1 两笔30元 | 原交易2笔、疑似问题1条、关联准确、不删除 |
| 2 正常并重放 | NORMAL和历史保留，不重新打开、不重新提醒 |
| 3 三笔两两满足 | AB/AC/BC三条、共享交易关联可定位、重放不增加 |
| 4 退100到60再40 | 满72h提醒；差4000分；最终验证解决；到账重放不累计 |
| 5 全额先到申请后到 | 信息齐全显示到账，逾期通知0条 |
| 6 持续不回复 | 首次count=0；24h前不追加；24/48h各一次；以后不催 |
| 7 明天提醒 | 不早于now+24h及既有约束；次数不清零；不堆消息 |
| 8 确认问题 | 仍待解决、无成功依据、退出无回复催办 |
| 9 关闭后到账 | CLOSED不变，事实更新，不继续发消息 |
| 10 中断恢复 | 问题、计数、时钟、任务和已发消息保留，不重复处理 |

必要边界：10分钟整/超1ms、72小时整/前1ms；跨用户同编号/权限；暂停多天恢复不补发；暂停期间足额恢复不误催；关闭先后；未知核实与重启；失败不计数；次数耗尽后的用户约定；确认问题后约定不重启自动催办；事件和业务编号内容冲突；缺失时间；real推进拒绝、库模式错配拒绝。

恢复分两层：

1. 自动快速验证：临时文件库、关闭Session/引擎、重新初始化并运行完整启动恢复。标注为“进程内恢复验证”。
2. 最终演示验证：停止真实服务进程，保留文件库，重新启动后查询并重放事实。记录启动和核验输出，标注为“真实进程重启验证”。

若第二层未做，报告注明未验证，不用第一层代替声称通过。真实模式另做短后台烟测：无用户消息也能执行周期检查；虚拟业务阈值不因此改短。

reports/acceptance.md或JSON包含场景、输入、预期、实际、证据位置、失败修复及复测。demo输出展示输入/时间/系统行为/最终状态/断言，存为文本证据；源码、数据、步骤共同组成可重放演示。可录5—10分钟视频，但不替代源码或运行说明。

## 10. 交付、优先级和当前代码差异

交付：源码、固定依赖与配置样例、README干净环境启动和操作说明、虚构数据、设计、验收报告、demo及实际输出、AI原始协作记录与索引、关键人工判断/个人贡献、组件复用和缺口说明。

AI记录包含豆包、Codex需求理解到编码测试修复的可取得原始输入输出和工具结果。索引/总结不能替代原文；导出缺失如实说明，不伪造，不提交认证秘密或无关私人数据。

实施顺序：

1. 对齐同步基础、schema和配置；建立最小测试与留存/幂等入口。
2. 重复检测及用户正常结论：场景1/2/3。
3. 退款乱序、累计和结案：场景4/5。
4. 提醒、约定、确认问题、暂停关闭：场景6/7/8/9。
5. 模拟提交/查询、未知核实、启动恢复：场景10及异常边界。
6. HTTP重放、全量回归、隔离检查、报告、运行说明与记录整理。

时间盒从实际开始计，最多4小时，不因提前收到题面或方案自动宣称已计时。预留至少最后30分钟用于交付核验和提交；文档和证据同步积累，最后不补造结果。

缩减优先级：自定义前端、图分组、商户管理、复杂故障脚本、多层抽象、额外录屏（已有等价重放时）。必要规则和数据留存能力不可用“增强”名义删除，来不及逐项记缺口且不算通过。

现有app/config.py、clock.py、database.py、models.py仍为早期骨架，本次修订只改文档：

- Numeric改整数分，普通DateTime改明确UTC毫秒；校验时区和配置，不静默降级。
- 全局编号唯一改用户范围，到账补用户归属、稳定arrival_no和复合FK。
- 退款容器允许信息缺失；Issue拆结论与跟进模式；移除必需cluster。
- Reminder写入即已发改任务和外部操作；增加接收、处理历史及恢复状态。
- 补API、鉴权、后台线程、通知模拟、恢复及测试。当前环境SQLAlchemy缺失，依赖未齐，不宣称可运行。
- create_all不迁移已有表；schema不兼容拒绝启动。开发可配置新库，旧数据保留，不自动删除或覆盖。
- 项目已初始化本地Git，基线d41e417；本版文档单独提交，便于审阅，不混入实现改动。

本文经用户审阅后再写实施计划和编码，所有验收通过声明必须有实际运行证据。