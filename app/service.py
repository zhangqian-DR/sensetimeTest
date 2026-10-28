"""账单业务入口：串行修改、原始事实留存、状态推进与外部效果核实。"""
import json
import threading
from uuid import uuid4
from sqlalchemy import select, func
from .models import (UserSettings, AppMeta, EventReceipt, ProcessingHistory, Transaction,
                     Refund, RefundArrival, Issue, IssueTransaction, ReminderTask, NotificationOperation)
from .services.events import dump, digest, normalize
from .notifications import SimulatedNotifications, BEHAVIORS
from .clock import format_time

HOUR = 3600000
ACTIVE = ('PENDING','SUBMITTING','UNKNOWN')
TERMINAL = ('NORMAL','RESOLVED','CLOSED')


class DomainError(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail
        super().__init__(str(detail))


class BillingService:
    def __init__(self, database, clock, notification_gateway=None):
        self.database, self.clock = database, clock
        self.lock = threading.Lock()
        self.gateway = notification_gateway or SimulatedNotifications(database, clock)
        with database.session_factory() as session:
            behavior = session.get(AppMeta, 'notification_behavior')
            if behavior and hasattr(self.gateway, 'behavior'):
                self.gateway.behavior = behavior.value

    def _history(self, session, user, result, *, receipt=None, issue=None, task=None, detail=None, before=None):
        session.add(ProcessingHistory(user_id=user, receipt_id=receipt, issue_id=issue,
            task_id=task, trigger='SERVICE', result=result, reason_code=result,
            detail_json=dump(detail or {}), before_json=dump(before) if before else None,
            processed_at=self.clock.now_ms()))

    @staticmethod
    def _row(session, model, user, id):
        row = session.scalar(select(model).where(model.id == id, model.user_id == user))
        if row is None:
            raise DomainError(404, 'Resource not found')
        return row

    def _record(self, user, event_type, payload):
        now = self.clock.now_ms()
        with self.database.session_factory.begin() as session:
            if session.get(UserSettings, user) is None:
                raise DomainError(404, 'User not found')
            record = EventReceipt(user_id=user, event_id=payload.get('event_id') if isinstance(payload,dict) and isinstance(payload.get('event_id'),str) else None,
                event_type=event_type, payload_json=dump(payload), payload_hash=digest({'type':event_type,'payload':payload}),
                received_at=now, processing_state='RECEIVED', is_canonical=False)
            session.add(record)
            session.flush()
            return record.id

    def receive(self, user_id, event_type, payload):
        with self.lock:
            return self._receive_locked(user_id, event_type, payload)

    def _receive_locked(self, user, event_type, payload):
        # 接收先独立提交，后续校验或业务事务失败也不丢失现场。
        receipt_id = self._record(user,event_type,payload)
        now = self.clock.now_ms()
        try:
            normalized = normalize(event_type,payload,now)
        except (ValueError, TypeError, OverflowError) as error:
            with self.database.session_factory.begin() as session:
                receipt = session.get(EventReceipt,receipt_id)
                receipt.processing_state = 'REJECTED'
                self._history(session,user,'INVALID',receipt=receipt_id,detail={'error':str(error)})
            raise DomainError(422,str(error)) from error
        try:
            with self.database.session_factory.begin() as session:
                receipt = session.get(EventReceipt,receipt_id)
                canonical = session.scalar(select(EventReceipt).where(EventReceipt.user_id==user,
                    EventReceipt.event_id==normalized['event_id'],EventReceipt.is_canonical.is_(True)))
                norm_hash = digest({'type':event_type,'payload':normalized})
                if canonical:
                    result = 'DUPLICATE' if canonical.payload_hash==norm_hash else 'CONFLICT'
                    receipt.canonical_receipt_id = canonical.id
                    receipt.processing_state=result
                    self._history(session,user,result,receipt=receipt_id,detail={'canonical_receipt_id':canonical.id})
                else:
                    receipt.payload_hash=norm_hash
                    receipt.is_canonical=True
                    receipt.occurred_at=next((normalized[k] for k in ('occurred_at','confirmed_at','arrived_at') if k in normalized),None)
                    result = self._apply(session,user,event_type,normalized,receipt_id,now)
                    if result not in ('CONFLICT','DUPLICATE'):
                        paused = session.get(UserSettings,user).paused
                        result = 'DEFERRED' if paused and event_type in ('TXN','REFUND_CONFIRMED','REFUND_ARRIVAL') else 'APPLIED'
                    receipt.processing_state=result
                    receipt.applied_at=now if result=='APPLIED' else None
                    self._history(session,user,result,receipt=receipt_id,detail={'event_type':event_type})
        except DomainError as error:
            with self.database.session_factory.begin() as session:
                receipt=session.get(EventReceipt,receipt_id)
                receipt.processing_state='REJECTED'
                self._history(session,user,'FAILED',receipt=receipt_id,detail={'error':error.detail})
            raise
        except Exception as error:
            with self.database.session_factory.begin() as session:
                self._history(session,user,'FAILED',receipt=receipt_id,detail={'error':str(error)})
            raise
        if result not in ('CONFLICT','DUPLICATE'):
            self._evaluate_locked()
        return {'receipt_id':receipt_id,'result':result,'received':True,'applied':result=='APPLIED'}

    def _apply(self, session, user, kind, p, receipt_id, now):
        if kind=='TXN':
            existing=session.scalar(select(Transaction).where(Transaction.user_id==user,Transaction.tx_no==p['tx_no']))
            values={k:p[k] for k in ('tx_no','merchant_id','amount_fen','occurred_at','currency')}
            if existing:
                return 'DUPLICATE' if all(getattr(existing,k)==v for k,v in values.items()) else 'CONFLICT'
            session.add(Transaction(user_id=user,source_receipt_id=receipt_id,created_at=now,
                                    merchant_name=p.get('merchant_name'),**values))
        elif kind in ('REFUND_CONFIRMED','REFUND_ARRIVAL'):
            # 先按流水查重再建退款容器，冲突输入只留日志，不污染事实表。
            if kind=='REFUND_ARRIVAL':
                prior=session.scalar(select(RefundArrival).where(RefundArrival.user_id==user,RefundArrival.arrival_no==p['arrival_no']))
                if prior:
                    prior_refund=session.get(Refund,prior.refund_id)
                    equal=prior_refund.refund_no==p['refund_no'] and prior.amount_fen==p['amount_fen'] and prior.arrived_at==p['arrived_at']
                    return 'DUPLICATE' if equal else 'CONFLICT'
            refund=session.scalar(select(Refund).where(Refund.user_id==user,Refund.refund_no==p['refund_no']))
            if refund is None:
                refund=Refund(user_id=user,refund_no=p['refund_no'],created_at=now,updated_at=now)
                session.add(refund)
                session.flush()
            if kind=='REFUND_CONFIRMED':
                if refund.confirmed_at is not None:
                    equal=refund.confirmed_at==p['confirmed_at'] and refund.expected_amount_fen==p['expected_amount_fen'] and refund.tx_no==p.get('tx_no')
                    return 'DUPLICATE' if equal else 'CONFLICT'
                refund.confirmed_at=p['confirmed_at']
                refund.expected_amount_fen=p['expected_amount_fen']
                refund.tx_no=p.get('tx_no')
                refund.confirmation_receipt_id=receipt_id
                refund.updated_at=now
            else:
                existing=session.scalar(select(RefundArrival).where(RefundArrival.user_id==user,RefundArrival.arrival_no==p['arrival_no']))
                if existing:
                    equal=existing.refund_id==refund.id and existing.amount_fen==p['amount_fen'] and existing.arrived_at==p['arrived_at']
                    return 'DUPLICATE' if equal else 'CONFLICT'
                session.add(RefundArrival(user_id=user,refund_id=refund.id,arrival_no=p['arrival_no'],
                    amount_fen=p['amount_fen'],arrived_at=p['arrived_at'],source_receipt_id=receipt_id,created_at=now))
                for issue in session.scalars(select(Issue).where(Issue.user_id==user,Issue.refund_id==refund.id)):
                    self._history(session,user,'FACT_ONLY_CLOSED' if issue.status=='CLOSED' else 'NEW_ARRIVAL',
                                  receipt=receipt_id,issue=issue.id,detail={'arrival_no':p['arrival_no'],'amount_fen':p['amount_fen']})
        elif kind=='SETTINGS':
            settings=session.get(UserSettings,user)
            settings.paused=p['paused']
            settings.updated_at=now
            if settings.paused:
                settings.paused_at=now
            else:
                settings.resumed_at=now
        elif kind=='USER_ACTION':
            self._action(session,user,p,receipt_id,now)
        session.flush()
        return 'APPLIED'

    def act(self,user_id,issue_id,payload):
        with self.lock:
            return self._receive_locked(user_id,'USER_ACTION',{**payload,'issue_id':issue_id})

    def set_paused(self,user_id,payload):
        with self.lock:
            return self._receive_locked(user_id,'SETTINGS',payload)

    def _cancel(self, session, issue, reason):
        for task in session.scalars(select(ReminderTask).where(ReminderTask.issue_id==issue.id,ReminderTask.status=='PENDING')):
            task.status='CANCELLED'
            task.updated_at=self.clock.now_ms()
            self._history(session,issue.user_id,'CANCELLED',issue=issue.id,task=task.id,detail={'reason':reason})
        session.flush()

    def _action(self,session,user,p,receipt_id,now):
        issue=self._row(session,Issue,user,p['issue_id'])
        action=p['action']
        before={'status':issue.status,'followup_mode':issue.followup_mode}
        if action=='REOPEN':
            if issue.status!='CLOSED':
                raise DomainError(409,'Only a closed issue can be explicitly reopened')
            if issue.resolution_basis:
                issue.status='RESOLVED'
                issue.followup_mode='STOPPED'
            else:
                closes=session.scalars(select(ProcessingHistory).where(ProcessingHistory.issue_id==issue.id,
                    ProcessingHistory.result=='CLOSE').order_by(ProcessingHistory.processed_at.desc())).all()
                old=json.loads(closes[0].before_json) if closes and closes[0].before_json else {}
                issue.status=old.get('status','OPEN')
                if issue.status not in ('OPEN','CONFIRMED_PROBLEM'):
                    issue.status='OPEN'
                issue.followup_mode='WAITING_FACT'
            issue.closed_at=None
        elif action=='CLOSE':
            if issue.status=='CLOSED':
                # 重复关闭仍保留输入，但不能覆盖首次有效关闭前的用户结论。
                self._history(session,user,'CLOSE_NO_CHANGE',receipt=receipt_id,issue=issue.id)
                return
            issue.status='CLOSED'
            issue.followup_mode='STOPPED'
            issue.closed_at=now
            issue.requested_remind_at=None
            self._cancel(session,issue,'User closed issue')
        elif issue.status in TERMINAL:
            raise DomainError(409,'Issue is terminal; reopen a closed issue explicitly first')
        elif action=='NORMAL':
            if issue.issue_type!='DUPLICATE':
                raise DomainError(422,'Normal purchases applies only to duplicate issues')
            issue.status='NORMAL'
            issue.followup_mode='STOPPED'
            issue.requested_remind_at=None
            self._cancel(session,issue,'User confirmed normal purchases')
        elif action=='CONFIRM_PROBLEM':
            issue.status='CONFIRMED_PROBLEM'
            issue.followup_mode='WAITING_FACT'
            issue.requested_remind_at=None
            self._cancel(session,issue,'User replied; wait for facts or explicit agreement')
        elif action=='RESOLVE':
            issue.status='RESOLVED'
            issue.followup_mode='STOPPED'
            issue.resolution_basis='USER_CONFIRMED'
            issue.resolution_detail_json=dump({'receipt_id':receipt_id,'statement':'用户确认已自行解决，非系统验证退款'})
            issue.resolved_at=now
            issue.requested_remind_at=None
            self._cancel(session,issue,'User confirmed resolution')
        elif action=='SNOOZE':
            target=now+24*HOUR if p.get('tomorrow') else p['remind_at']
            if target<=now:
                raise DomainError(422,'remind_at must be in the future')
            issue.not_before_at=max(target,issue.not_before_at or 0,
                                    (issue.last_sent_at+24*HOUR) if issue.last_sent_at is not None else 0)
            issue.requested_remind_at=issue.not_before_at
            issue.schedule_generation+=1
            issue.followup_mode='USER_SCHEDULED'
            self._cancel(session,issue,'Replaced by new user agreement')
        issue.updated_at=now
        self._history(session,user,action,receipt=receipt_id,issue=issue.id,
                      detail={'action':action,'schedule_generation':issue.schedule_generation},before=before)
    @staticmethod
    def _amount(session,refund):
        return session.scalar(select(func.coalesce(func.sum(RefundArrival.amount_fen),0)).where(
            RefundArrival.user_id==refund.user_id,RefundArrival.refund_id==refund.id))

    def _detect(self,session,user,now):
        txns=session.scalars(select(Transaction).where(Transaction.user_id==user).order_by(Transaction.occurred_at)).all()
        for index,left in enumerate(txns):
            for right in txns[index+1:]:
                if right.occurred_at-left.occurred_at>600000:
                    break
                if left.merchant_id!=right.merchant_id or left.amount_fen!=right.amount_fen:
                    continue
                key=dump(sorted([left.id,right.id]))
                existing=session.scalar(select(Issue).where(Issue.user_id==user,Issue.issue_type=='DUPLICATE',Issue.identity_key==key))
                if existing:
                    continue
                issue=Issue(user_id=user,issue_type='DUPLICATE',identity_key=key,
                    reason='疑似重复消费：同商户同金额、不同交易编号，间隔不超过10分钟；尚未证实错误扣款',
                    created_at=now,updated_at=now)
                session.add(issue)
                session.flush()
                session.add_all([IssueTransaction(user_id=user,issue_id=issue.id,transaction_id=t.id) for t in (left,right)])
                self._history(session,user,'ISSUE_CREATED',issue=issue.id,detail={'transaction_ids':[left.id,right.id]})
        for refund in session.scalars(select(Refund).where(Refund.user_id==user)):
            if refund.confirmed_at is None:
                continue
            received=self._amount(session,refund)
            issue=session.scalar(select(Issue).where(Issue.user_id==user,Issue.issue_type=='REFUND_OVERDUE',Issue.identity_key==refund.id))
            # 先检查足额再检查逾期，乱序数据齐全时不发过时催款。
            if received>=refund.expected_amount_fen:
                if issue and issue.status not in TERMINAL:
                    issue.status='RESOLVED'
                    issue.followup_mode='STOPPED'
                    issue.resolution_basis='FULL_REFUND_VERIFIED'
                    issue.resolution_detail_json=dump({'refund_no':refund.refund_no,'expected_amount_fen':refund.expected_amount_fen,
                                                      'received_amount_fen':received})
                    issue.resolved_at=now
                    issue.updated_at=now
                    issue.requested_remind_at=None
                    self._cancel(session,issue,'Full refund verified')
                    self._history(session,user,'FULL_REFUND_VERIFIED',issue=issue.id,detail=json.loads(issue.resolution_detail_json))
            elif now>=refund.confirmed_at+72*HOUR and issue is None:
                issue=Issue(user_id=user,issue_type='REFUND_OVERDUE',identity_key=refund.id,refund_id=refund.id,
                    reason='退款申请确认已满72小时，仍未足额到账',created_at=now,updated_at=now)
                session.add(issue)
                session.flush()
                self._history(session,user,'ISSUE_CREATED',issue=issue.id,detail={'refund_no':refund.refund_no})

    def evaluate(self):
        with self.lock:
            self._evaluate_locked()

    def _evaluate_locked(self):
        now=self.clock.now_ms()
        # 只核实上一轮已有提交；未知结果绝不再调用submit。
        with self.database.session_factory() as session:
            unresolved=[(t.id,t.operation_id,t.issue_id) for t in session.scalars(select(ReminderTask).where(
                ReminderTask.status.in_(('SUBMITTING','UNKNOWN'))))]
        reconciled=set()
        for task_id,operation_id,issue_id in unresolved:
            try:
                result=self.gateway.query(operation_id)
            except Exception as error:
                result={'state':'UNKNOWN','detail':str(error)}
            self._complete(task_id,result)
            reconciled.add(issue_id)
        with self.database.session_factory.begin() as session:
            users=session.scalars(select(UserSettings).where(UserSettings.paused.is_(False))).all()
            for settings in users:
                self._detect(session,settings.user_id,now)
                for receipt in session.scalars(select(EventReceipt).where(EventReceipt.user_id==settings.user_id,
                        EventReceipt.processing_state=='DEFERRED',EventReceipt.is_canonical.is_(True))):
                    receipt.processing_state='APPLIED'
                    receipt.applied_at=now
                    self._history(session,settings.user_id,'APPLIED_AFTER_RESUME',receipt=receipt.id)
            session.flush()
            for issue in session.scalars(select(Issue)):
                if issue.status in TERMINAL:
                    self._cancel(session,issue,'Terminal issue')
                    continue
                if session.get(UserSettings,issue.user_id).paused or issue.id in reconciled:
                    continue
                active=session.scalar(select(ReminderTask).where(ReminderTask.issue_id==issue.id,ReminderTask.status.in_(ACTIVE)))
                if active:
                    continue
                if issue.followup_mode=='USER_SCHEDULED' and issue.requested_remind_at is not None:
                    kind,key,due='USER_SCHEDULED','USER:'+str(issue.schedule_generation),issue.requested_remind_at
                elif issue.followup_mode=='AWAITING_REPLY':
                    if issue.initial_sent_at is None:
                        kind,key,due='INITIAL','INITIAL',max(now,issue.not_before_at or 0)
                    elif issue.auto_followup_count<2:
                        kind='AUTO_FOLLOWUP'
                        key='AUTO:'+str(issue.auto_followup_count+1)
                        due=max(issue.last_sent_at+24*HOUR,issue.not_before_at or 0)
                    else:
                        continue
                else:
                    continue
                existing=session.scalar(select(ReminderTask).where(ReminderTask.issue_id==issue.id,ReminderTask.task_key==key))
                if existing:
                    continue
                task=ReminderTask(user_id=issue.user_id,issue_id=issue.id,kind=kind,task_key=key,
                                  due_at=due,created_at=now,updated_at=now)
                session.add(task)
                session.flush()
                self._history(session,issue.user_id,'TASK_CREATED',issue=issue.id,task=task.id,detail={'kind':kind,'due_at':due})
        with self.database.session_factory() as session:
            due=[t.id for t in session.scalars(select(ReminderTask).where(ReminderTask.status=='PENDING',ReminderTask.due_at<=now))
                 if t.issue_id not in reconciled]
        for task_id in due:
            self._send(task_id)

    def _send(self,task_id):
        now=self.clock.now_ms()
        with self.database.session_factory.begin() as session:
            task=session.get(ReminderTask,task_id)
            issue=session.get(Issue,task.issue_id)
            if task.status!='PENDING' or session.get(UserSettings,issue.user_id).paused:
                return
            if issue.status in TERMINAL:
                self._cancel(session,issue,'Stopped before submit')
                return
            if issue.issue_type=='REFUND_OVERDUE':
                refund=session.get(Refund,issue.refund_id)
                remaining=max(refund.expected_amount_fen-self._amount(session,refund),0)
                if remaining==0:
                    self._cancel(session,issue,'Refund arrived before sending')
                    return
                content={'issue_id':issue.id,'message':issue.reason,'remaining_amount_fen':remaining,'refund_no':refund.refund_no}
            else:
                content={'issue_id':issue.id,'message':issue.reason,'status':issue.status}
            # 先持久化操作编号和提交意图，再调用外部模拟器，重启后可查询而非重发。
            task.status='SUBMITTING'
            task.submitted_at=now
            task.content_json=dump(content)
            task.updated_at=now
            self._history(session,issue.user_id,'SUBMITTING',issue=issue.id,task=task.id,detail={'operation_id':task.operation_id})
            operation_id,user,tid=task.operation_id,task.user_id,task.id
        try:
            result=self.gateway.submit(operation_id,user,tid,content)
        except Exception as error:
            result={'state':'UNKNOWN','detail':str(error)}
        self._complete(task_id,result)

    def _complete(self,task_id,result):
        now=self.clock.now_ms()
        with self.database.session_factory.begin() as session:
            task=session.get(ReminderTask,task_id)
            issue=session.get(Issue,task.issue_id)
            state=result.get('state','UNKNOWN')
            if state=='SENT':
                task.status='SENT'
                task.sent_at=result.get('delivered_at') or now
                task.confirmed_at=now
                if task.counted_at is None:
                    if task.kind=='INITIAL':
                        issue.initial_sent_at=task.sent_at
                    elif task.kind=='AUTO_FOLLOWUP':
                        issue.auto_followup_count+=1
                    elif task.kind=='USER_SCHEDULED':
                        generation=int(task.task_key.split(':')[1])
                        # 旧消息核实成功不得清除未知期间接受的新版本约定。
                        if generation==issue.schedule_generation:
                            issue.requested_remind_at=None
                            if issue.status not in TERMINAL:
                                issue.followup_mode='WAITING_FACT'
                    issue.last_sent_at=task.sent_at
                    task.counted_at=now
            elif state in ('FAILED','NOT_SUBMITTED'):
                task.status='FAILED'
                task.error_code=state
            else:
                task.status='UNKNOWN'
                task.error_code='UNKNOWN'
            task.error_detail=result.get('detail')
            task.updated_at=now
            receipt=EventReceipt(user_id=issue.user_id,event_id=str(uuid4()),event_type='NOTIFICATION_RESULT',
                source='SIMULATOR',payload_json=dump({'operation_id':task.operation_id,**result}),
                payload_hash=digest(result),received_at=now,is_canonical=False,processing_state='APPLIED',applied_at=now)
            session.add(receipt)
            session.flush()
            self._history(session,issue.user_id,task.status,receipt=receipt.id,issue=issue.id,task=task.id,detail=result)
    def advance_clock(self,target_ms):
        with self.lock:
            if not hasattr(self.clock,'advance_to') or self.database.settings.time_mode!='simulated':
                raise DomainError(403,'Real mode cannot advance time')
            try:
                value=self.clock.advance_to(target_ms)
            except (ValueError,TypeError) as error:
                raise DomainError(422,str(error)) from error
            self._evaluate_locked()
            return value

    def set_notification_behavior(self,behavior):
        with self.lock:
            if behavior not in BEHAVIORS:
                raise DomainError(422,'Invalid simulated notification behavior')
            with self.database.session_factory.begin() as session:
                meta=session.get(AppMeta,'notification_behavior')
                if meta:
                    meta.value=behavior
                else:
                    session.add(AppMeta(key='notification_behavior',value=behavior))
            self.gateway.behavior=behavior
            return {'behavior':behavior}

    def recover(self):
        with self.lock:
            with self.database.session_factory() as session:
                pending=[(r.id,r.user_id,r.event_type,json.loads(r.payload_json)) for r in session.scalars(
                    select(EventReceipt).where(EventReceipt.processing_state.in_(('RECEIVED','ACCEPTED'))))]
            # 直接恢复已落盘的原接收，不在每次重启时制造替代输入。
            for receipt_id,user,kind,payload in pending:
                try:
                    normalized=normalize(kind,payload,self.clock.now_ms())
                    with self.database.session_factory.begin() as session:
                        receipt=session.get(EventReceipt,receipt_id)
                        canonical=session.scalar(select(EventReceipt).where(EventReceipt.user_id==user,
                            EventReceipt.event_id==normalized['event_id'],EventReceipt.is_canonical.is_(True),EventReceipt.id!=receipt_id))
                        norm_hash=digest({'type':kind,'payload':normalized})
                        if canonical:
                            receipt.canonical_receipt_id=canonical.id
                            receipt.processing_state='DUPLICATE' if canonical.payload_hash==norm_hash else 'CONFLICT'
                        else:
                            receipt.is_canonical=True
                            receipt.payload_hash=norm_hash
                            result=self._apply(session,user,kind,normalized,receipt_id,self.clock.now_ms())
                            receipt.processing_state='CONFLICT' if result=='CONFLICT' else ('DEFERRED' if session.get(UserSettings,user).paused else 'APPLIED')
                        self._history(session,user,'RECOVERED_INPUT',receipt=receipt_id,detail={'state':receipt.processing_state})
                except (ValueError,DomainError) as error:
                    with self.database.session_factory.begin() as session:
                        receipt=session.get(EventReceipt,receipt_id)
                        receipt.processing_state='REJECTED'
                        self._history(session,user,'RECOVERY_REJECTED',receipt=receipt_id,detail={'error':str(error)})
            self._evaluate_locked()

    @staticmethod
    def _serialize(row):
        data={column.name:getattr(row,column.name) for column in row.__table__.columns}
        for key,value in list(data.items()):
            if key.endswith('_json') and value is not None:
                data[key[:-5]]=json.loads(value)
            if key.endswith('_at') and type(value) is int:
                data[key+'_beijing']=format_time(value)
        return data

    def get_issue(self,user_id,issue_id):
        with self.database.session_factory() as session:
            issue=self._row(session,Issue,user_id,issue_id)
            result=self._serialize(issue)
            txns=session.scalars(select(Transaction).join(IssueTransaction,Transaction.id==IssueTransaction.transaction_id).where(
                IssueTransaction.issue_id==issue_id,Transaction.user_id==user_id)).all()
            result['transactions']=[self._serialize(t) for t in txns]
            txids=[t.id for t in txns]
            related=session.scalars(select(IssueTransaction.issue_id).where(IssueTransaction.user_id==user_id,
                IssueTransaction.transaction_id.in_(txids),IssueTransaction.issue_id!=issue_id)).all() if txids else []
            result['related_issue_ids']=sorted(set(related))
            result['remaining_amount_fen']=None
            if issue.refund_id:
                refund=session.get(Refund,issue.refund_id)
                result['refund']=self._refund_detail(session,refund)
                result['remaining_amount_fen']=result['refund']['remaining_amount_fen']
                if refund.tx_no:
                    transaction=session.scalar(select(Transaction).where(Transaction.user_id==user_id,Transaction.tx_no==refund.tx_no))
                    if transaction:
                        result['transactions']=[self._serialize(transaction)]
            result['history']=[self._serialize(h) for h in session.scalars(select(ProcessingHistory).where(
                ProcessingHistory.user_id==user_id,ProcessingHistory.issue_id==issue_id).order_by(ProcessingHistory.processed_at))]
            result['paused']=session.get(UserSettings,user_id).paused
            result['evaluation_note']='暂停中，新事实尚未评估' if result['paused'] else None
            return result

    def list_issues(self,user_id):
        with self.database.session_factory() as session:
            ids=session.scalars(select(Issue.id).where(Issue.user_id==user_id).order_by(Issue.created_at,Issue.id)).all()
        return [self.get_issue(user_id,id) for id in ids]

    def _refund_detail(self,session,refund):
        result=self._serialize(refund)
        received=self._amount(session,refund)
        result['received_amount_fen']=received
        result['remaining_amount_fen']=None if refund.expected_amount_fen is None else max(refund.expected_amount_fen-received,0)
        result['overpaid_amount_fen']=None if refund.expected_amount_fen is None else max(received-refund.expected_amount_fen,0)
        result['tracking_status']=('WAITING_INFORMATION' if refund.confirmed_at is None else
            ('FULLY_RECEIVED' if received>=refund.expected_amount_fen else
             ('OVERDUE' if self.clock.now_ms()>=refund.confirmed_at+72*HOUR else 'TRACKING')))
        result['arrivals']=[self._serialize(a) for a in session.scalars(select(RefundArrival).where(RefundArrival.refund_id==refund.id))]
        return result

    def get_refund(self,user_id,refund_no):
        with self.database.session_factory() as session:
            refund=session.scalar(select(Refund).where(Refund.user_id==user_id,Refund.refund_no==refund_no))
            if refund is None:
                raise DomainError(404,'Refund not found')
            return self._refund_detail(session,refund)

    def list_refunds(self,user_id):
        with self.database.session_factory() as session:
            return [self._refund_detail(session,r) for r in session.scalars(select(Refund).where(Refund.user_id==user_id))]

    def list_reminders(self,user_id):
        with self.database.session_factory() as session:
            return [self._serialize(r) for r in session.scalars(select(ReminderTask).where(ReminderTask.user_id==user_id).order_by(ReminderTask.created_at))]

    def list_notifications(self,user_id):
        with self.database.session_factory() as session:
            return [self._serialize(r) for r in session.scalars(select(NotificationOperation).where(
                NotificationOperation.user_id==user_id,NotificationOperation.provider_state=='SENT').order_by(NotificationOperation.submitted_at))]

    def get_event(self,user_id,receipt_id):
        with self.database.session_factory() as session:
            result=self._serialize(self._row(session,EventReceipt,user_id,receipt_id))
            result['history']=[self._serialize(h) for h in session.scalars(select(ProcessingHistory).where(
                ProcessingHistory.user_id==user_id,ProcessingHistory.receipt_id==receipt_id))]
            return result

    def history(self,user_id,issue_id):
        return self.get_issue(user_id,issue_id)['history']