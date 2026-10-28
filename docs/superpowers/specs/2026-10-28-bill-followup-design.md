# 账单问题持续跟进系统技术设计

- 日期：2026-10-28
- 状态：待用户审阅的目标设计；本文不代表功能已实现或验收已通过。
- 交付约束：北京时间14:00—18:00，最多4小时；到上限如实交付成果与缺口。
- 技术：Python、FastAPI、SQLAlchemy、SQLite、pytest；Swagger作为第一版交互界面。
- 范围：仅疑似重复消费、退款逾期；所有数据、身份、通知均为本地模拟，不操作真实账户。

## 1. 已确认的产品规则

1. 同用户、同商户、同金额、不同交易编号且时间差≤10分钟，每个无序交易对形成一条疑似问题。三笔两两满足则三条，展示共享交易关联。保留原交易，不声称证实错误扣款。
2. 按退款编号归集申请确认及到账。自申请确认满72小时，仍未全额到账才提醒；先到的到账保留，信息齐全再判断。部分到账显示差额，重放不累计。
3. 正常购买、确认有问题、用户确认解决、关闭分开记录。确认有问题不等于解决；重复问题只能以用户明确确认解决结案，不能声称已验证退款。退款问题可以凭全额到账验证解决。
4. 首次提醒不计追加；未回复时距实际上一条成功提醒至少24小时，最多自动追加两次。部分到账、延期、暂停、恢复和重启不重置计数。
5. 确认有问题后停止无回复催办，等待新事实或用户明确约定。用户主动约定的提醒独立于自动追加次数；次数耗尽后仍可约定一次提醒，但不因此恢复自动催办。
6. 明天提醒取 max(既有最早允许提醒时间, 当前时间+24小时)。只给日期缺时间时返回澄清需求，不自行猜测。
7. 暂停期间保存输入事实，标记待评估，不创建问题、不自动结案、不发送提醒。恢复后按最新事实统一评估，不逐条补发暂停期间过期提醒。
8. 关闭后保存新事实、更新资金事实展示及历史，维持关闭，不继续自动推进。重新开启必须用户明确要求，保留计数，先评估最新事实，不立即补旧消息；仍未解决时等待新约定。
9. 原始输入与业务处理分层：无论正常、重复、暂停、关闭、失败，都保留接收与处理证据；同一事实只生效一次。
10. 模拟外部通知提供提交与查询。成功才计发送次数；失败如实记录，本版不自动重试；未知结果后台核实，不盲目重复提交。

## 2. 架构和模块边界

单进程FastAPI服务，单后台检查循环，SQLite文件持久化。第一版只启一个worker，避免多进程定时任务竞争；数据库唯一约束和事务仍作为防重底线。

建议模块：

| 模块 | 职责 |
|---|---|
| config / clock | 校验配置、真实/模拟时钟、持久化模拟时间 |
| auth | 配置测试Token到用户的映射，限制当前用户作用域 |
| ingestion | 持久化每次接收、识别重复/冲突、规范化业务事实 |
| detection | 两两重复交易检测、退款事实汇总与逾期判断 |
| issue_service | 用户动作、暂停恢复、重开、状态转换和历史 |
| reminder_service | 计算资格、安排任务、发送前检查、成功计数 |
| notification_gateway | 模拟提交/查询、模拟收件箱与故障注入 |
| scheduler | 启动恢复、周期检查、推进时间后的自动检查 |
| api / schemas | Swagger接口、结构化输入输出、用户范围校验 |
| demo / tests | 独立场景重放、断言、报告和重启验证 |

服务层负责事务与副作用；规则函数以显式事实、状态和now输入返回判断，不在纯规则函数里发送消息。所有入口复用同一业务服务，演示不能直接修改最终状态凑结果。

顺序：接收并落日志 → 去重/校验 → 落业务事实 → 汇总最新事实 → 转换允许转换的问题状态 → 取消失效提醒 → 创建合资格任务 → 发送前复检 → 模拟发送/核实 → 更新计数和历史。

## 3. 时间、金额、身份和配置

### 3.1 配置示例

```dotenv
APP_TIME_MODE=simulated
APP_TIMEZONE=Asia/Shanghai
APP_CHECK_INTERVAL_SECONDS=1
APP_SIMULATED_START=2026-10-28T14:00:00+08:00
APP_DB_PATH=./data/simulated.db
APP_TEST_TOKENS_JSON={"demo-token-a":"user-a","demo-token-b":"user-b"}
APP_ENABLE_DEMO_CONTROLS=true
```

- mode只允许real/simulated，无效配置启动失败，不能静默降级。切换模式修改配置并重启。
- real默认data/real.db，simulated默认data/simulated.db。数据库保存mode，模式不匹配拒绝启动，避免手工误配同一文件。
- real使用真实时钟，禁止推进时间；simulated启动后时间保持不动，由推进接口触发同一检查流程。后台仍检查当前模拟时间下的任务。
- 模拟起始时间只首次使用，之后从数据库读取；推进必须单调不减，推进和检查串行，重启保留。
- 输入ISO 8601带时区；统一转为UTC整数毫秒持久化，响应展示北京时间及+08:00。Windows部署依赖包含tzdata。
- 金额只使用INTEGER分。API使用amount_fen等明确字段，拒绝负数及不合法金额；人民币固定CNY。
- 10分钟、72小时、24小时、自动追加上限2是固定业务规则，不通过配置加速；演示用虚拟时间。
- 身份由Bearer测试Token解析；请求正文的用户字段不能覆盖身份。无效Token返回401，跨用户资源统一404。时间推进是全局演示管理权限，不开放给普通用户；仅simulated且开启演示控制时可用。
- 本版是本地演示认证，不宣称生产身份认证。测试Token不用于任何真实服务。

## 4. 表结构设计

### 4.1 通用约定

- 主键除用户/元数据外采用应用生成UUID TEXT，便于在调用外部服务前确定操作编号。
- *_at为UTC毫秒INTEGER；*_json为规范JSON TEXT；金额INTEGER分；布尔INTEGER CHECK IN (0,1)。
- 所有用户业务表包含user_id。跨表关联使用(user_id,id)复合外键，目标增加UNIQUE(user_id,id)，防止跨用户关联。
- 启用PRAGMA foreign_keys=ON、WAL、busy_timeout。业务状态、历史、计数、任务创建在同一事务提交。
- 原始日志与处理日志追加写，不随问题关闭删除。仅虚构数据；Token、Authorization和密钥不写日志。原始输入只指业务输入，不含认证秘密。
- 用户触发的修改接口要求event_id，防止动作重放生成重复约定。读取接口无业务副作用。

### 4.2 app_users / user_settings / app_meta

| 表 | 字段及约束 |
|---|---|
| app_users | id TEXT PK；display_name TEXT NOT NULL；created_at INTEGER NOT NULL |
| user_settings | user_id TEXT PK FK users；paused INTEGER NOT NULL DEFAULT 0；paused_at、resumed_at可空；updated_at NOT NULL |
| app_meta | key TEXT PK；value TEXT NOT NULL；updated_at INTEGER NOT NULL |

app_meta保存schema_version、time_mode、virtual_now。Token映射读取配置，不明文写数据库。用户设置变更也进入输入/处理历史。

### 4.3 event_receipts：每次输入的原始接收记录

| 字段 | 类型/约束 | 含义 |
|---|---|---|
| id | TEXT PK | 每次接收都有新编号，重复请求也新增 |
| user_id | TEXT NOT NULL FK | 已认证归属 |
| event_id | TEXT，可空 | 外部稳定事件编号；缺失也留记录 |
| event_type | TEXT NOT NULL | TXN / REFUND_CONFIRMED / REFUND_ARRIVAL / USER_ACTION / SETTINGS / NOTIFICATION_RESULT |
| source | TEXT NOT NULL | API / BACKGROUND / SIMULATOR |
| payload_json | TEXT NOT NULL | 不含密钥的业务原文 |
| payload_hash | TEXT NOT NULL | 对规范业务内容做哈希，不含接收时间 |
| occurred_at | INTEGER，可空 | 事实发生时间 |
| received_at | INTEGER NOT NULL | 接收时间 |
| trace_id | TEXT NOT NULL | 关联调用与处理 |

索引(user_id,event_id)、(user_id,received_at)。此表不对event_id加唯一约束，因为每次接收都必须留存。无效业务输入在认证通过后也记录；未认证请求只记录不含凭证的安全摘要，不能让其指定任意用户归属。

### 4.4 accepted_events / processing_history：事实幂等与处理轨迹

accepted_events：id PK；user_id NOT NULL；event_id NOT NULL；event_type NOT NULL；payload_hash NOT NULL；first_receipt_id FK；state NOT NULL；accepted_at NOT NULL；applied_at可空；UNIQUE(user_id,event_id)。

state = ACCEPTED / DEFERRED / APPLIED / REJECTED。暂时失败仍保留待处理状态，错误写入处理历史，供恢复重评。相同event_id和哈希为重放；同编号不同内容为CONFLICT，保留第二份原文，不覆盖已确认事实。

processing_history：id PK；user_id；receipt_id可空；accepted_event_id可空；issue_id可空；trigger；result；reason_code；detail_json；before_json可空；after_json可空；processed_at。记录DUPLICATE、CONFLICT、INVALID、DEFERRED_PAUSED、FACT_ONLY_CLOSED、APPLIED、FAILED等结果。每次处理尝试追加一行，不能靠覆盖一个状态还原全部过程。

两层去重：event_id防同一输入重复生效；交易/退款/到账的业务唯一键防不同event_id包装同一事实。不同event_id但相同业务编号且内容冲突同样拒绝覆盖并留痕。

### 4.5 transactions：交易事实

字段：id PK；user_id NOT NULL；tx_no TEXT NOT NULL；merchant_id TEXT NOT NULL；merchant_name TEXT可空；amount_fen INTEGER NOT NULL CHECK(amount_fen>0)；currency TEXT NOT NULL DEFAULT CNY；occurred_at INTEGER NOT NULL；source_event_id FK；created_at INTEGER NOT NULL。

约束：UNIQUE(user_id,tx_no)、UNIQUE(user_id,id)。索引(user_id,merchant_id,amount_fen,occurred_at)。不必单独建设商户表；检测按明确商户编号，不按模糊名字猜测。

### 4.6 refunds：退款归集容器，支持到账先到

字段：id PK；user_id NOT NULL；refund_no TEXT NOT NULL；tx_no TEXT可空；expected_amount_fen INTEGER可空 CHECK为空或>0；confirmed_at INTEGER可空；confirmation_event_id可空；created_at、updated_at NOT NULL。

约束：UNIQUE(user_id,refund_no)、UNIQUE(user_id,id)。confirmed_at与expected_amount_fen应同时具备或同时缺失。到账先到时创建只有user/refund_no的容器；缺失金额不是0，不进行逾期或全额判断。

tx_no先保留外部关联编号，不强制交易必须先到；可在交易到达后通过(user_id,tx_no)解析关联。此处不跨用户关联，不因交易缺失丢失退款事实。

### 4.7 refund_arrivals：每笔到账事实

字段：id PK；user_id NOT NULL；refund_id TEXT NOT NULL；arrival_no TEXT NOT NULL；amount_fen INTEGER NOT NULL CHECK>0；arrived_at INTEGER NOT NULL；source_event_id FK；created_at NOT NULL。

约束：UNIQUE(user_id,arrival_no)、UNIQUE(user_id,id)；复合FK(user_id,refund_id)→refunds(user_id,id)。arrival_no定义为同一用户下稳定的到账流水号；不同合法到账不得用金额+时间生成去重键，以免误吞。

到账总额SUM(amount_fen)实时汇总，不直接盲增一个余额。差额max(应退−累计到账,0)；超过应退金额时单独显示超额及异常说明，不丢弃事实。可判已足额到账，但不自动执行退回等额外操作。

### 4.8 issues：问题处理与提醒控制

| 字段 | 类型/约束 | 说明 |
|---|---|---|
| id / user_id | TEXT PK / NOT NULL | 问题及归属 |
| issue_type | TEXT NOT NULL | DUPLICATE / REFUND_OVERDUE |
| identity_key | TEXT NOT NULL | 无序交易对或退款业务身份 |
| status | TEXT NOT NULL | OPEN / CONFIRMED_PROBLEM / NORMAL / RESOLVED / CLOSED |
| followup_mode | TEXT NOT NULL | AWAITING_REPLY / WAITING_FACT / USER_SCHEDULED / STOPPED |
| reason | TEXT NOT NULL | 可读触发原因 |
| refund_id | TEXT可空 | 退款问题关联 |
| auto_followup_count | INTEGER NOT NULL DEFAULT 0 CHECK 0..2 | 仅成功的自动追加 |
| initial_sent_at | INTEGER可空 | 首次成功提醒 |
| last_sent_at | INTEGER可空 | 最近任意成功提醒 |
| not_before_at | INTEGER可空 | 当前最早允许提醒时间 |
| schedule_generation | INTEGER NOT NULL DEFAULT 0 | 每次新约定递增，生成稳定任务键 |
| resolution_basis | TEXT可空 | USER_CONFIRMED / FULL_REFUND_VERIFIED |
| resolution_detail_json | TEXT可空 | 用户动作/退款/到账事实引用 |
| resolved_at / closed_at | INTEGER可空 | 分开记录，不互相冒充 |
| created_at / updated_at | INTEGER NOT NULL | 时间 |

约束：UNIQUE(user_id,issue_type,identity_key)、UNIQUE(user_id,id)。索引(user_id,status)、(followup_mode,not_before_at)。重复问题identity_key为按稳定顺序排列的两个交易内部id的JSON编码；退款问题用refund_id，避免字符串拼接歧义。

NORMAL不是RESOLVED，关闭不等于解决。CLOSED优先阻止自动转换；补到全额退款时status仍CLOSED，但资金事实显示全额到账，历史注明关闭后收到。仅重新开启后才按最新事实转为验证解决。

### 4.9 issue_transactions：问题与交易关联

字段：user_id；issue_id；transaction_id；PRIMARY KEY(issue_id,transaction_id)。两组复合FK分别关联同用户issues和transactions。

DUPLICATE创建时同一事务插入恰好两条关联。关联问题通过共享transaction_id查询，不维护cluster_id，避免新交易桥接时分组错乱。退款问题通过refund.tx_no展示关联交易；交易缺失则明确显示待补充。

### 4.10 reminder_tasks：提醒意图、提交和核实状态

| 字段 | 类型/约束 | 含义 |
|---|---|---|
| id / user_id / issue_id | TEXT | 同用户复合FK |
| kind | TEXT NOT NULL | INITIAL / AUTO_FOLLOWUP / USER_SCHEDULED |
| sequence_no | INTEGER NOT NULL | 首次0；追加1/2；用户约定用generation |
| task_key | TEXT NOT NULL | INITIAL、AUTO:1、AUTO:2、USER:n |
| due_at | INTEGER NOT NULL | 最早执行时间 |
| status | TEXT NOT NULL | PENDING / SUBMITTING / SENT / FAILED / UNKNOWN / CANCELLED |
| operation_id | TEXT NOT NULL UNIQUE | 创建任务时预分配的外部操作号 |
| content_json | TEXT NOT NULL | 发送时使用的事实快照与措辞 |
| submitted_at / sent_at | INTEGER可空 | 提交与确认成功分开 |
| counted_at | INTEGER可空 | 成功已计入问题计数的标记 |
| next_verify_at | INTEGER可空 | 后台核实时间 |
| error_code / error_detail | TEXT可空 | 失败或未知原因 |
| source_event_id | TEXT可空 | 用户约定来源，首次/自动可空 |
| created_at / updated_at | INTEGER NOT NULL | 时间 |

约束：UNIQUE(user_id,issue_id,task_key)、UNIQUE(user_id,id)。索引(status,due_at)、(status,next_verify_at)。SQLite部分唯一索引：每个问题最多一个status IN (PENDING,SUBMITTING,UNKNOWN)的有效任务，避免未知期间再安排消息。

用户再次延期：更新尚未提交的同一待发送安排并追加历史，不堆出多条。已提交/未知任务不能假装撤销；记录新约定意图在事件中，先核实旧操作，再根据最新状态重新安排。失败任务不自动重发；若用户重新约定，创建新generation任务，保留失败任务。

计数更新：条件更新任务为SENT且counted_at为空 → 对INITIAL设置首次时间，对AUTO增加一次计数，对USER不修改追加数 → 写counted_at/last_sent_at/历史，同一事务提交。重复查询成功不会重复计数。

### 4.11 notification_operations / notification_messages：模拟外部服务

notification_operations：operation_id TEXT PK；user_id；task_id；scenario SUCCESS/FAIL/UNKNOWN_THEN_SUCCESS/UNKNOWN_THEN_FAILURE；provider_state NOT_SUBMITTED/PENDING/SENT/FAILED；query_count；resolve_after_queries；submitted_at；completed_at可空；error_detail可空。

notification_messages：id PK；operation_id TEXT UNIQUE FK；user_id；recipient；content_json；delivered_at。

模拟发送服务的数据独立于业务任务状态，均落盘；业务SUBMITTING先提交事务，再调用模拟服务。UNKNOWN_THEN_SUCCESS可在提交时已产生消息但暂不返回确认，后续按operation_id查询成功；因此未知时绝不可再次创建操作发送。模拟服务对相同operation_id幂等。

未知操作查询返回NOT_SUBMITTED时可确认未产生外部效果，标明确失败；不得自动重新提交。持续不可核实时保持UNKNOWN、显示原因并继续有间隔查询，不伪造失败或成功。

模拟收件箱是可观察外部效果，任务表中的PENDING不是“已发送”。关闭/暂停后依然允许只读核实此前已提交的操作，不创建新外部效果。

## 5. 检测与状态转换

### 5.1 疑似重复

每笔新交易查询同用户、商户、金额、时间范围±600000ms的其他交易，逐对检查。用排序内部id的identity_key插入，唯一约束兜底。09:00/09:05/09:11只有两对；09:00/09:05/09:09有三对。

既有问题无论NORMAL/RESOLVED/CLOSED均不因重放重建或打开。新的独立交易对不受旧问题关闭影响。措辞固定“疑似重复消费，待用户确认”。

### 5.2 退款

信息不齐：WAITING_INFORMATION，作为派生展示状态，不触发逾期。
信息齐全：先汇总到账，足额则显示到账；若已有非关闭退款问题，验证解决并取消未提交任务。若从未逾期，仅展示退款跟踪事实，不凭空制造“曾逾期”问题。
未足额且now≥confirmed_at+72h：创建/更新退款逾期问题。部分到账只改变事实和差额，不重置次数，不恢复确认有问题后的无回复催办。

### 5.3 用户动作

| 动作 | 结果 |
|---|---|
| 正常购买 | 仅重复类；status=NORMAL；停止提醒，取消未提交任务 |
| 确有问题 | status=CONFIRMED_PROBLEM；WAITING_FACT；停止自动催办 |
| 明天/指定时间提醒 | 保留问题处理结论；USER_SCHEDULED；创建或延期一条约定 |
| 已自行解决 | RESOLVED；USER_CONFIRMED；保存用户动作依据；停止提醒 |
| 关闭 | CLOSED；STOPPED；取消PENDING，保留已发送和未知操作 |
| 重新开启 | 用户显式动作；保留次数；先核对事实；未解决则WAITING_FACT，不补发旧任务 |

用户可以直接确认解决，不强制先确认有问题。RESOLVED退款的后续事实可继续补证据，但不覆盖原用户确认依据；历史可增加全额到账事实。

### 5.4 调度资格

- 自动：状态允许、用户未暂停、AWAITING_REPLY、无未知/提交中任务、未到上限。
- 首次：从未成功首次提醒且存在待提醒问题。
- 自动追加：count<2且now≥last_sent_at+24h，并满足not_before_at。
- 用户约定：到due_at且问题仍有效、用户未暂停；发送后WAITING_FACT，不恢复自动催办。
- 延期后不通过while循环补齐24/48小时任务；一次检查最多发送该问题一条，后续自动追加从实际成功发送时起至少24小时。
- 每次真正提交前重新读取最新状态、暂停设置和退款事实，取消已失效任务。

## 6. 并发、关闭顺序和恢复

第一版用单进程全局async业务锁串行修改接口、时间推进、调度与模拟提交；锁内只调用本地有界模拟服务，不接真实网络。请求先持久化接收记录，是否“接受关闭”以关闭事务成功提交为准。

关闭提交先于发送：发送前复检阻止提交；发送提交先于关闭：如实保留外部操作，不声称撤回。SUBMITTING/UNKNOWN在关闭后继续查询既有operation_id，结果计入真实历史，但不安排下一条。

启动恢复：读取mode/clock → 扫描SUBMITTING/UNKNOWN并查询既有操作 → 恢复DEFERRED/ACCEPTED输入 → 对未暂停用户汇总最新事实 → 取消失效任务 → 按当前状态安排至多一条合资格提醒。暂停用户不业务推进，只核实既有提交。

数据库提交失败回滚业务事务，原始receipt已独立提交；补写FAILED处理记录。若数据库本身不可用，返回503并明确未接受，不声称已持久化；保留可取得的进程错误日志。进程崩溃后可由已落盘receipt恢复处理。

不承诺真实跨系统exactly-once；本版借稳定operation_id、幂等模拟服务、恢复查询实现可验证的单次外部效果。后续替换真实服务时需服务商支持查询/幂等能力，不能仅靠本地事务保证。

## 7. Swagger接口约定

| 接口 | 用途 |
|---|---|
| GET /health | 模式、版本、后台运行状态，不泄露Token |
| GET /me | 当前虚构用户 |
| POST /events/transactions | 输入交易，含event_id |
| POST /events/refund-confirmations | 输入应退金额、确认时间、退款编号 |
| POST /events/refund-arrivals | 输入arrival_no、到账金额和时间 |
| GET /issues / GET /issues/{id} | 原因、相关交易、资金事实、状态、结案依据 |
| GET /issues/{id}/history | 原始事件引用和处理时间线 |
| POST /issues/{id}/actions | NORMAL / CONFIRM_PROBLEM / SNOOZE / RESOLVE / CLOSE / REOPEN；event_id必填 |
| PUT /me/settings | 暂停/恢复；event_id必填 |
| GET /refunds | 未齐全、未逾期、部分/全额到账跟踪 |
| GET /reminders / GET /notifications | 任务状态及模拟收件箱 |
| GET /events/{id} | 当前用户原始业务事件与处理结果 |
| GET /demo/clock / POST /demo/clock/advance | 受限演示管理接口；推进后自动检查 |
| PUT /demo/notification-behavior | 仅演示管理权限，设置后续模拟结果 |

动作通过枚举与条件字段校验。SNOOZE支持tomorrow或remind_at；指定值缺少时间/时区返回422及明确说明，不创建任务。普通用户不能设置provider状态、追加次数或问题结案依据。

返回同时说明received、applied、deferred、duplicate、conflict；收到不等于生效。后台失败在health及处理记录可查，不静默吞异常。GET只展示持久化事实，不为了刷新页面偷偷触发推进。

## 8. 验收与可重放演示

pytest每场景独立数据库和时钟。重启场景使用临时文件库与实际进程重启，不能仅用内存对象模拟。demo脚本通过HTTP业务接口操作，按顺序输出输入、时间、系统行为、断言和证据；不直接改数据库最终状态。

| 场景 | 关键断言 |
|---|---|
| 1 两笔30元 | 保留2交易；1疑似问题；关联准确 |
| 2 正常并重放 | NORMAL保留；不重开、不新增提醒 |
| 3 三笔两两满足 | AB/AC/BC共3；重放不增；共享交易可定位 |
| 4 100退款到60再40 | 72h提醒；先差4000分；最终足额验证解决；重放不增 |
| 5 全额先到 | 补申请后到账展示正确；逾期消息0 |
| 6 不回复 | 首次count=0；24h前不追加；24/48h两次；以后不催 |
| 7 延期 | 不早于now+24h及既有约束；次数保留；不堆任务 |
| 8 确认问题 | 待解决，结案依据为空；不继续无回复催办 |
| 9 关闭后到账 | CLOSED保留；事实更新；不发消息 |
| 10 中断恢复 | 问题、计数、时钟、已发消息保留；不重复处理 |

补充：10分钟整/超1ms、72小时整/前1ms；跨用户同编号；暂停跨多个到期点恢复；暂停期间到账恢复后不误催；关闭先于发送/发送先于关闭；未知期间重启核实；明确失败不计数；次数耗尽后的主动约定；确认问题后约定不恢复催办；事件内容冲突；指定日期缺时间；real禁止推进；模式错配拒绝启动。

输出reports/acceptance.md或JSON，包含场景、输入、预期、实际、通过/失败、证据位置、修复复测记录。未实现不写通过。demo_output.txt是运行证据，不能替代demo源码、数据和复现说明。5—10分钟录屏可后补，等价可重放脚本优先。

## 9. 交付与时间预算

交付：源码、固定依赖、.env.example、README启动/输入/推进/查询/验收步骤、虚构测试数据、设计、验收报告、demo脚本和实际输出、AI原始协作记录及索引、复用及个人贡献说明、已知缺口。

AI记录覆盖本次需求分析、豆包会话、设计、编码、测试与失败修正；保存可取得原始记录，索引不能代替原文。无法完整导出的范围如实说明，不伪造、不提交密钥或无关私人数据。

建议时间预算：前20分钟确认设计和测试骨架；55分钟事实/去重/检测；55分钟动作/调度/控制；40分钟通知/恢复/隔离；35分钟验收修复；25分钟文档和演示；最后10分钟检查提交。具体以实际开始时间和剩余时间调整，交付材料同步积累。

缩减顺序：页面美化/自定义前端、独立商户管理、复杂框架抽象、录屏（已有等价重放时）。不得把持久化、隔离、去重、未知结果、停止条件悄悄降级；来不及则逐项标缺口。所有完成/通过声明基于实际运行证据。

## 10. 当前项目差异与实施边界

本次只新增设计文档，不修改已有app代码。当前已存在config.py、clock.py、database.py、models.py，需实施时逐项核对：

- 金额Numeric改INTEGER分；普通DateTime改明确UTC毫秒或等价可靠转换。
- 交易/退款全局唯一改用户复合唯一；到账补用户归属、稳定流水号及复合关联。
- 退款容器允许申请信息尚未齐全；区别confirmed_at与提交申请时间。
- Issue拆分处理状态与followup_mode，移除必需cluster依赖，增加计数/约定约束。
- Reminder“写入即已发”改为任务状态、稳定operation_id和模拟外部结果。
- 增加原始接收、幂等事实、追加处理轨迹和模拟消息表。
- 配置无效时明确失败；校验数据库模式；补真实后台检查、授权和启动恢复。
- create_all不会修改已有表；开发阶段使用新版本演示数据库，保留旧库备份，不自动删除原数据。schema_version不兼容拒绝启动并说明迁移/新库步骤。

当前目录没有Git仓库，因此本文不创建提交、不初始化仓库；后续如需版本管理再明确执行。本文需用户审阅后再进入实施计划和编码。