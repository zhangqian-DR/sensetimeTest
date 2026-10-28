"""Swagger输入样例和结构说明；业务校验在原始输入留存之后执行。"""
TXN_EXAMPLE = {'event_id': 'evt-t1', 'tx_no': 't1', 'merchant_id': 'shop-1', 'amount_fen': 3000, 'occurred_at': '2026-10-28T09:00:00+08:00'}
REFUND_EXAMPLE = {'event_id': 'evt-r1', 'refund_no': 'r1', 'expected_amount_fen': 10000, 'confirmed_at': '2026-10-28T14:00:00+08:00', 'tx_no': 't1'}
ARRIVAL_EXAMPLE = {'event_id': 'evt-a1', 'refund_no': 'r1', 'arrival_no': 'a1', 'amount_fen': 6000, 'arrived_at': '2026-10-31T14:00:00+08:00'}
ACTION_EXAMPLE = {'event_id': 'evt-action-1', 'action': 'SNOOZE', 'tomorrow': True}
SETTINGS_EXAMPLE = {'event_id': 'evt-settings-1', 'paused': True}
ACTION_SCHEMA = {
    'type': 'object',
    'required': ['event_id', 'action'],
    'properties': {
        'event_id': {'type': 'string', 'description': 'Stable idempotency identifier for this user action'},
        'action': {'type': 'string', 'enum': ['NORMAL', 'CONFIRM_PROBLEM', 'SNOOZE', 'RESOLVE', 'CLOSE', 'REOPEN']},
        'tomorrow': {'type': 'boolean', 'description': 'SNOOZE only: at least 24 hours from now, respecting existing lower bound'},
        'remind_at': {'type': 'string', 'format': 'date-time', 'description': 'SNOOZE only: complete datetime with timezone; date alone is rejected'},
    },
    'description': 'Business validation runs only after raw input is retained. NORMAL means two legitimate purchases; CONFIRM_PROBLEM remains unresolved; RESOLVE records user confirmation; CLOSE stops tracking; REOPEN requires explicit user instruction.',
}