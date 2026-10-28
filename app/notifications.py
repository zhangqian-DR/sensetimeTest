"""模拟外部服务：实际投递独立落盘，不把业务任务状态当作发送结果。"""
from .models import NotificationOperation
from .services.events import dump

BEHAVIORS = {'SUCCESS','FAIL','UNKNOWN_THEN_SUCCESS','UNKNOWN_THEN_FAILURE'}


class SimulatedNotifications:
    def __init__(self, database, clock):
        self.database, self.clock = database, clock
        self.behavior = 'SUCCESS'

    def submit(self, operation_id, user_id, task_id, content):
        with self.database.session_factory.begin() as session:
            operation = session.get(NotificationOperation, operation_id)
            if operation is not None:
                return self._result(operation)
            behavior = self.behavior
            state = 'SENT' if behavior in ('SUCCESS','UNKNOWN_THEN_SUCCESS') else ('FAILED' if behavior == 'FAIL' else 'PENDING')
            operation = NotificationOperation(operation_id=operation_id, user_id=user_id,
                task_id=task_id, behavior=behavior, provider_state=state, recipient=user_id,
                content_json=dump(content), submitted_at=self.clock.now_ms(),
                delivered_at=self.clock.now_ms() if state == 'SENT' else None,
                error_detail='Simulated delivery failure' if state == 'FAILED' else None)
            session.add(operation)
            session.flush()
            if behavior.startswith('UNKNOWN'):
                return {'state':'UNKNOWN','detail':'Submitted; provider response unavailable'}
            return self._result(operation)

    def query(self, operation_id):
        with self.database.session_factory.begin() as session:
            operation = session.get(NotificationOperation, operation_id)
            if operation is None:
                return {'state':'NOT_SUBMITTED','detail':'Authoritative local simulator has no operation'}
            if operation.provider_state == 'PENDING':
                operation.provider_state = 'FAILED'
                operation.error_detail = 'Simulated provider confirmed failure'
            return self._result(operation)

    @staticmethod
    def _result(operation):
        return {'state':operation.provider_state, 'delivered_at':operation.delivered_at,
                'detail':operation.error_detail}