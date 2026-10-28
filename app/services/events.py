"""原始接收独立提交后，再规范化和校验业务输入。"""
import hashlib
import json
from ..clock import parse_time_ms


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def digest(value):
    return hashlib.sha256(dump(value).encode('utf-8')).hexdigest()


def required_string(payload, name):
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + ' must be a nonempty string')
    return value


def positive_money(payload, name):
    value = payload.get(name)
    if type(value) is not int or value <= 0 or value > 10**14:
        raise ValueError(name + ' must be positive integer fen, at most 100000000000000')
    return value


def normalize(event_type, payload, now):
    if not isinstance(payload, dict):
        raise ValueError('Expected a JSON object')
    required_string(payload, 'event_id')
    p = dict(payload)
    if 'user_id' in p:
        raise ValueError('Identity comes from authentication, not payload.user_id')
    if event_type == 'TXN':
        for field in ('tx_no', 'merchant_id'):
            required_string(p, field)
        positive_money(p, 'amount_fen')
        if p.get('merchant_name') is not None and not isinstance(p['merchant_name'],str):
            raise ValueError('merchant_name must be a string or null')
        p['occurred_at'] = parse_time_ms(required_string(p, 'occurred_at'))
        if p.get('currency', 'CNY') != 'CNY':
            raise ValueError('Only CNY is supported')
        p['currency'] = 'CNY'
    elif event_type == 'REFUND_CONFIRMED':
        required_string(p, 'refund_no')
        positive_money(p, 'expected_amount_fen')
        if p.get('tx_no') is not None:
            required_string(p,'tx_no')
        p['confirmed_at'] = parse_time_ms(required_string(p, 'confirmed_at'))
    elif event_type == 'REFUND_ARRIVAL':
        required_string(p, 'refund_no')
        required_string(p, 'arrival_no')
        positive_money(p, 'amount_fen')
        p['arrived_at'] = parse_time_ms(required_string(p, 'arrived_at'))
    elif event_type == 'USER_ACTION':
        required_string(p, 'issue_id')
        if p.get('action') not in ('NORMAL','CONFIRM_PROBLEM','SNOOZE','RESOLVE','CLOSE','REOPEN'):
            raise ValueError('Unknown action')
        if p['action'] == 'SNOOZE':
            if p.get('tomorrow') is True and 'remind_at' not in p:
                pass
            elif p.get('remind_at') and not p.get('tomorrow'):
                p['remind_at'] = parse_time_ms(required_string(p, 'remind_at'))
                # 是否过期只在首次应用时检查，旧事件重放仍应命中幂等结果。
            else:
                raise ValueError('Provide tomorrow=true OR remind_at with date, time and timezone')
    elif event_type == 'SETTINGS':
        if type(p.get('paused')) is not bool:
            raise ValueError('paused must be a boolean')
    else:
        raise ValueError('Unsupported event type: ' + str(event_type))
    return p