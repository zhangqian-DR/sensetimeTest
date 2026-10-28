"""同步HTTP接口薄层；所有业务状态转换统一调用BillingService。"""
from datetime import datetime
import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from .auth import current_user, demo_admin
from .schemas import ACTION_SCHEMA, ACTION_EXAMPLE, ARRIVAL_EXAMPLE, REFUND_EXAMPLE, SETTINGS_EXAMPLE, TXN_EXAMPLE

router = APIRouter()
logger = logging.getLogger('bill_followup.audit')
User = Annotated[str, Depends(current_user)]
Admin = Annotated[str, Depends(demo_admin)]


def invoke(request, method, *args):
    try:
        return getattr(request.app.state.service, method)(*args)
    except SQLAlchemyError as error:
        # 接收可能已独立提交，数据库故障不能伪称未收到或允许换编号盲重试。
        logger.error('数据库操作失败，完成状态待核实；错误类型=%s', type(error).__name__)
        raise HTTPException(503, '数据库不可用，无法确认本次操作是否完成；请按原event_id核对记录，勿更换编号盲目重试。') from error
    except Exception as error:
        # 业务服务先保留输入及处理结果，再由HTTP层映射领域错误。
        if type(error).__name__ == 'DomainError' and hasattr(error, 'status_code'):
            raise HTTPException(error.status_code, error.detail) from error
        raise


def now_ms(request):
    service = request.app.state.service
    return service.clock.now_ms()


@router.get('/health', tags=['System'])
def health(request: Request):
    scheduler = request.app.state.scheduler
    return {'status': 'degraded' if scheduler.last_error else 'ok', 'time_mode': request.app.state.settings.time_mode,
            'now_ms': now_ms(request), 'background_running': scheduler.is_alive,
            'last_checked_at': scheduler.last_checked_at, 'last_error': scheduler.last_error,
            'single_worker_required': True, 'notifications_simulated': True}


@router.get('/me', tags=['User'])
def me(user: User):
    return {'user_id': user, 'identity_type': 'local-test-token'}


@router.post('/events/transactions', tags=['Events'])
def transaction(request: Request, user: User, payload: dict = Body(..., examples=[TXN_EXAMPLE])):
    return invoke(request, 'receive', user, 'TXN', payload)


@router.post('/events/refund-confirmations', tags=['Events'])
def refund_confirmation(request: Request, user: User, payload: dict = Body(..., examples=[REFUND_EXAMPLE])):
    return invoke(request, 'receive', user, 'REFUND_CONFIRMED', payload)


@router.post('/events/refund-arrivals', tags=['Events'])
def refund_arrival(request: Request, user: User, payload: dict = Body(..., examples=[ARRIVAL_EXAMPLE])):
    return invoke(request, 'receive', user, 'REFUND_ARRIVAL', payload)


@router.get('/issues', tags=['Issues'])
def issues(request: Request, user: User):
    return invoke(request, 'list_issues', user)


@router.get('/issues/{issue_id}', tags=['Issues'])
def issue(request: Request, issue_id: str, user: User):
    return invoke(request, 'get_issue', user, issue_id)


@router.get('/issues/{issue_id}/history', tags=['Issues'])
def issue_history(request: Request, issue_id: str, user: User):
    return invoke(request, 'history', user, issue_id)


@router.post('/issues/{issue_id}/actions', tags=['Issues'])
def action(request: Request, issue_id: str, user: User, payload: dict = Body(..., examples=[ACTION_EXAMPLE], json_schema_extra=ACTION_SCHEMA)):
    return invoke(request, 'act', user, issue_id, payload)


@router.put('/me/settings', tags=['User'])
def settings(request: Request, user: User, payload: dict = Body(..., examples=[SETTINGS_EXAMPLE])):
    return invoke(request, 'set_paused', user, payload)


@router.get('/refunds', tags=['Facts'])
def refunds(request: Request, user: User):
    return invoke(request, 'list_refunds', user)


@router.get('/refunds/{refund_no}', tags=['Facts'])
def refund(request: Request, refund_no: str, user: User):
    return invoke(request, 'get_refund', user, refund_no)


@router.get('/reminders', tags=['Messages'])
def reminders(request: Request, user: User):
    return invoke(request, 'list_reminders', user)


@router.get('/notifications', tags=['Messages'])
def notifications(request: Request, user: User):
    return invoke(request, 'list_notifications', user)


@router.get('/events/{receipt_id}', tags=['Events'])
def event(request: Request, receipt_id: str, user: User):
    return invoke(request, 'get_event', user, receipt_id)


@router.get('/demo/clock', tags=['Demo controls'])
def get_clock(request: Request, admin: Admin):
    return {'now_ms': now_ms(request), 'time_mode': request.app.state.settings.time_mode}


def target_from_payload(payload, current):
    if 'target' in payload or 'target_ms' in payload:
        if any(key in payload for key in ('hours', 'minutes', 'seconds')):
            raise ValueError('Choose target or relative duration, not both')
        if 'target_ms' in payload:
            target = payload['target_ms']
            if type(target) is not int:
                raise ValueError('target_ms must be an integer')
        else:
            value = payload['target']
            if not isinstance(value, str) or 'T' not in value:
                raise ValueError('Specify a complete ISO datetime, including time and timezone')
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                raise ValueError('Specify timezone explicitly')
            if parsed.microsecond % 1000:
                raise ValueError('Time precision may not exceed milliseconds')
            target = int(parsed.timestamp() * 1000)
    else:
        values = [payload.get(key, 0) for key in ('hours', 'minutes', 'seconds')]
        if not any(key in payload for key in ('hours', 'minutes', 'seconds')):
            raise ValueError('Provide target datetime or hours/minutes/seconds')
        if any(type(value) not in (int, float) or value < 0 for value in values):
            raise ValueError('Relative duration must be non-negative numeric values')
        target = current + int((values[0] * 3600 + values[1] * 60 + values[2]) * 1000)
    if target < current:
        raise ValueError('Clock cannot move backwards')
    return target


@router.post('/demo/clock/advance', tags=['Demo controls'])
def advance_clock(request: Request, admin: Admin, payload: dict = Body(..., examples=[{'hours': 24}])):
    before = now_ms(request)
    try:
        target = target_from_payload(payload, before)
    except (ValueError, TypeError, OverflowError) as error:
        raise HTTPException(422, str(error)) from error
    result = invoke(request, 'advance_clock', target)
    logger.info('demo-clock actor=demo-admin before_ms=%s after_ms=%s', before, result)
    return {'now_ms': result, 'time_mode': request.app.state.settings.time_mode}


@router.put('/demo/notification-behavior', tags=['Demo controls'])
def notification_behavior(request: Request, admin: Admin, payload: dict = Body(..., examples=[{'behavior': 'UNKNOWN_THEN_SUCCESS'}])):
    behavior = payload.get('behavior')
    if behavior not in ('SUCCESS', 'FAIL', 'UNKNOWN_THEN_SUCCESS', 'UNKNOWN_THEN_FAILURE'):
        raise HTTPException(422, 'Unknown simulated notification behavior')
    invoke(request, 'set_notification_behavior', behavior)
    logger.info('demo-notification actor=demo-admin behavior=%s', behavior)
    return {'behavior': behavior, 'simulated': True}