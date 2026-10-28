"""Business acceptance: test-first; each fixture owns an isolated file database."""
import importlib.util
from datetime import datetime, timedelta

import pytest

BASE = 1793167200000
HOUR = 3600000


def iso(ms):
    return datetime.fromtimestamp(ms / 1000).astimezone().isoformat()


def tx(no, minute=0, **changes):
    payload = dict(event_id='tx-' + no, tx_no=no, merchant_id='shop',
                   amount_fen=3000, occurred_at=iso(BASE + minute * 60000))
    payload.update(changes)
    return payload


def conf(no='r', **changes):
    payload = dict(event_id='confirm-' + no, refund_no=no,
                   expected_amount_fen=10000, confirmed_at=iso(BASE))
    payload.update(changes)
    return payload


def arr(no, amount=6000, refund='r', **changes):
    payload = dict(event_id='arrival-' + no, refund_no=refund, arrival_no=no,
                   amount_fen=amount, arrived_at=iso(BASE + 72 * HOUR))
    payload.update(changes)
    return payload


@pytest.fixture
def service(tmp_path):
    assert importlib.util.find_spec('app.service'), 'BillingService not implemented'
    from app.config import load_settings
    from app.database import create_database
    from app.clock import make_clock
    from app.service import BillingService
    settings = load_settings({'APP_DB_PATH': str(tmp_path / 'test.db'),
                              'APP_TIME_MODE': 'simulated'})
    database = create_database(settings)
    database.initialize()
    result = BillingService(database, make_clock(database, settings))
    yield result
    database.dispose()


def pair(service):
    service.receive('user-a', 'TXN', tx('a'))
    service.receive('user-a', 'TXN', tx('b', 5))
    return service.list_issues('user-a')[0]['id']


def action(service, issue, name, **extra):
    return service.act('user-a', issue,
                       dict(event_id=name + '-' + str(extra), action=name, **extra))


def test_pair_and_normal_replay(service):
    issue = pair(service)
    assert len(service.list_issues('user-a')) == 1
    detail = service.get_issue('user-a', issue)
    assert len(detail['transactions']) == 2
    assert '疑似' in detail['reason']
    action(service, issue, 'NORMAL')
    service.receive('user-a', 'TXN', tx('a'))
    service.receive('user-a', 'TXN', tx('b', 5))
    assert service.get_issue('user-a', issue)['status'] == 'NORMAL'
    assert len(service.list_issues('user-a')) == 1


@pytest.mark.parametrize('minutes,expected', [([0,5,9],3),([0,5,11],2),([0,10],1),([0,10.001],0)])
def test_pairs_boundary_and_relation(service, minutes, expected):
    for index, minute in enumerate(minutes):
        service.receive('user-a', 'TXN', tx(str(index), minute))
    issues = service.list_issues('user-a')
    assert len(issues) == expected
    for index, minute in enumerate(minutes):
        service.receive('user-a', 'TXN', tx(str(index), minute))
    assert len(service.list_issues('user-a')) == expected
    if expected == 3:
        assert all(len(service.get_issue('user-a', i['id'])['related_issue_ids']) == 2 for i in issues)


def test_user_isolation_and_same_numbers(service):
    service.receive('user-a', 'TXN', tx('a'))
    service.receive('user-b', 'TXN', tx('a'))
    service.receive('user-b', 'TXN', tx('b', 5))
    assert service.list_issues('user-a') == []
    issue = service.list_issues('user-b')[0]
    from app.service import DomainError
    with pytest.raises(DomainError) as error:
        service.get_issue('user-a', issue['id'])
    assert error.value.status_code == 404


def test_refund_partial_full_and_replay(service):
    service.receive('user-a', 'REFUND_CONFIRMED', conf())
    service.advance_clock(BASE + 72 * HOUR - 1)
    assert service.list_issues('user-a') == []
    service.advance_clock(BASE + 72 * HOUR)
    assert len(service.list_issues('user-a')) == 1
    service.receive('user-a', 'REFUND_ARRIVAL', arr('a1'))
    assert service.get_refund('user-a', 'r')['remaining_amount_fen'] == 4000
    service.receive('user-a', 'REFUND_ARRIVAL', arr('a2', 4000))
    service.receive('user-a', 'REFUND_ARRIVAL', arr('a2', 4000))
    detail = service.get_refund('user-a', 'r')
    assert detail['received_amount_fen'] == 10000
    issue = service.list_issues('user-a')[0]
    assert issue['status'] == 'RESOLVED'
    assert issue['resolution_basis'] == 'FULL_REFUND_VERIFIED'


def test_arrival_before_confirmation_no_stale_message(service):
    service.advance_clock(BASE + 80 * HOUR)
    service.receive('user-a', 'REFUND_ARRIVAL', arr('a1', 10000))
    assert service.get_refund('user-a', 'r')['remaining_amount_fen'] is None
    service.receive('user-a', 'REFUND_CONFIRMED', conf())
    assert service.get_refund('user-a', 'r')['remaining_amount_fen'] == 0
    assert service.list_notifications('user-a') == []


def test_only_two_followups(service):
    issue = pair(service)
    assert len(service.list_notifications('user-a')) == 1
    for hour, count in [(23,0),(24,1),(47,1),(48,2),(96,2)]:
        service.advance_clock(BASE + hour * HOUR)
        assert service.get_issue('user-a', issue)['auto_followup_count'] == count
    assert len(service.list_notifications('user-a')) == 3


def test_snooze_and_explicit_after_quota(service):
    issue = pair(service)
    service.advance_clock(BASE + 24 * HOUR)
    action(service, issue, 'SNOOZE', tomorrow=True)
    service.advance_clock(BASE + 47 * HOUR)
    assert len(service.list_notifications('user-a')) == 2
    service.advance_clock(BASE + 48 * HOUR)
    assert len(service.list_notifications('user-a')) == 3
    assert service.get_issue('user-a', issue)['auto_followup_count'] == 1
    service.advance_clock(BASE + 96 * HOUR)
    assert len(service.list_notifications('user-a')) == 3


def test_confirm_problem_is_not_resolution(service):
    issue = pair(service)
    action(service, issue, 'CONFIRM_PROBLEM')
    service.advance_clock(BASE + 100 * HOUR)
    result = service.get_issue('user-a', issue)
    assert result['status'] == 'CONFIRMED_PROBLEM'
    assert result['resolution_basis'] is None
    assert len(service.list_notifications('user-a')) == 1


def test_closed_refund_keeps_facts(service):
    service.receive('user-a', 'REFUND_CONFIRMED', conf())
    service.advance_clock(BASE + 72 * HOUR)
    issue = service.list_issues('user-a')[0]['id']
    action(service, issue, 'CLOSE')
    service.receive('user-a', 'REFUND_ARRIVAL', arr('a1', 10000))
    service.advance_clock(BASE + 150 * HOUR)
    assert service.get_issue('user-a', issue)['status'] == 'CLOSED'
    assert service.get_refund('user-a', 'r')['received_amount_fen'] == 10000
    assert len(service.list_notifications('user-a')) == 1
    action(service, issue, 'REOPEN')
    assert service.get_issue('user-a', issue)['status'] == 'RESOLVED'


def test_pause_preserves_inputs_and_no_catchup(service):
    issue = pair(service)
    service.set_paused('user-a', {'event_id':'pause','paused':True})
    service.advance_clock(BASE + 100 * HOUR)
    assert len(service.list_notifications('user-a')) == 1
    service.set_paused('user-a', {'event_id':'resume','paused':False})
    assert len(service.list_notifications('user-a')) == 2
    assert service.get_issue('user-a', issue)['auto_followup_count'] == 1
    service.evaluate()
    assert len(service.list_notifications('user-a')) == 2


def test_pause_refund_latest_fact_before_evaluate(service):
    service.set_paused('user-a', {'event_id':'pause','paused':True})
    service.receive('user-a', 'REFUND_CONFIRMED', conf())
    service.advance_clock(BASE + 100 * HOUR)
    service.receive('user-a', 'REFUND_ARRIVAL', arr('a1', 10000))
    assert service.list_issues('user-a') == []
    service.set_paused('user-a', {'event_id':'resume','paused':False})
    assert service.list_notifications('user-a') == []
    assert service.get_refund('user-a', 'r')['remaining_amount_fen'] == 0


def test_unknown_reconciles_once_and_close_does_not_retract(service):
    service.set_notification_behavior('UNKNOWN_THEN_SUCCESS')
    issue = pair(service)
    assert service.list_reminders('user-a')[0]['status'] == 'UNKNOWN'
    assert len(service.list_notifications('user-a')) == 1
    action(service, issue, 'CLOSE')
    service.evaluate()
    service.evaluate()
    task = service.list_reminders('user-a')[0]
    assert task['status'] == 'SENT'
    assert service.get_issue('user-a', issue)['status'] == 'CLOSED'
    assert len(service.list_notifications('user-a')) == 1


def test_failed_not_counted_or_retried(service):
    service.set_notification_behavior('FAIL')
    issue = pair(service)
    service.advance_clock(BASE + 100 * HOUR)
    service.evaluate()
    assert service.get_issue('user-a', issue)['auto_followup_count'] == 0
    assert service.list_notifications('user-a') == []
    assert len(service.list_reminders('user-a')) == 1
    assert service.list_reminders('user-a')[0]['status'] == 'FAILED'


def test_conflicts_logged_not_overwritten(service):
    service.receive('user-a', 'TXN', tx('a'))
    result = service.receive('user-a', 'TXN', tx('a', amount_fen=4000))
    assert result['result'] == 'CONFLICT'
    other = tx('a', amount_fen=5000, event_id='different-event')
    assert service.receive('user-a', 'TXN', other)['result'] == 'CONFLICT'
    from app.models import EventReceipt, Transaction
    from sqlalchemy import select
    with service.database.session_factory() as session:
        assert len(session.scalars(select(EventReceipt)).all()) == 3
        assert session.scalar(select(Transaction)).amount_fen == 3000


def test_restart_preserves_state(service):
    issue = pair(service)
    service.advance_clock(BASE + 24 * HOUR)
    from app.service import BillingService
    from app.clock import make_clock
    from app.database import create_database
    settings = service.database.settings
    service.database.dispose()
    new_database = create_database(settings)
    new_database.initialize()
    restarted = BillingService(new_database, make_clock(new_database, settings))
    restarted.recover()
    assert restarted.get_issue('user-a', issue)['auto_followup_count'] == 1
    assert len(restarted.list_notifications('user-a')) == 2
    assert restarted.clock.now_ms() == BASE + 24 * HOUR
    restarted.receive('user-a','TXN',tx('a'))
    assert len(restarted.list_issues('user-a')) == 1
    new_database.dispose()


def test_snooze_missing_time_and_negative_amount_retained(service):
    from app.service import DomainError
    issue = pair(service)
    with pytest.raises(DomainError) as error:
        action(service,issue,'SNOOZE',remind_at='2026-10-30')
    assert error.value.status_code == 422
    with pytest.raises(DomainError):
        service.receive('user-a','TXN',tx('invalid',amount_fen=-1))
    from app.models import EventReceipt
    from sqlalchemy import select
    with service.database.session_factory() as session:
        receipts = session.scalars(select(EventReceipt)).all()
        assert any(r.processing_state == 'REJECTED' for r in receipts)

def test_different_event_same_fact_is_reported_duplicate(service):
    service.receive('user-a','TXN',tx('a'))
    replay=tx('a',event_id='new-envelope')
    assert service.receive('user-a','TXN',replay)['result']=='DUPLICATE'


def test_scheduled_action_replay_after_date_is_idempotent(service):
    issue=pair(service)
    payload={'event_id':'explicit-date','action':'SNOOZE','remind_at':iso(BASE+30*HOUR)}
    service.act('user-a',issue,payload)
    service.advance_clock(BASE+40*HOUR)
    assert service.act('user-a',issue,payload)['result']=='DUPLICATE'
    assert len(service.list_notifications('user-a'))==2


def test_explicit_reminder_after_auto_quota_is_not_new_auto_cycle(service):
    issue=pair(service)
    service.advance_clock(BASE+24*HOUR)
    service.advance_clock(BASE+48*HOUR)
    action(service,issue,'SNOOZE',tomorrow=True)
    service.advance_clock(BASE+72*HOUR)
    service.advance_clock(BASE+120*HOUR)
    assert service.get_issue('user-a',issue)['auto_followup_count']==2
    assert len(service.list_notifications('user-a'))==4


def test_duplicate_resolution_stays_user_confirmed(service):
    issue=pair(service)
    action(service,issue,'RESOLVE')
    result=service.get_issue('user-a',issue)
    assert result['status']=='RESOLVED'
    assert result['resolution_basis']=='USER_CONFIRMED'
    assert result['resolution_detail']['receipt_id']


def test_new_pair_after_close_is_independent(service):
    first=pair(service)
    action(service,first,'CLOSE')
    service.receive('user-a','TXN',tx('c',9))
    issues=service.list_issues('user-a')
    assert len(issues)==3
    assert sum(i['status']=='CLOSED' for i in issues)==1


def test_unknown_failure_stops_without_retry(service):
    service.set_notification_behavior('UNKNOWN_THEN_FAILURE')
    issue=pair(service)
    assert service.list_reminders('user-a')[0]['status']=='UNKNOWN'
    service.evaluate()
    service.advance_clock(BASE+100*HOUR)
    assert service.list_reminders('user-a')[0]['status']=='FAILED'
    assert len(service.list_reminders('user-a'))==1
    assert service.get_issue('user-a',issue)['auto_followup_count']==0
    assert service.list_notifications('user-a')==[]


def test_close_before_due_prevents_submit(service):
    issue=pair(service)
    action(service,issue,'SNOOZE',tomorrow=True)
    action(service,issue,'CLOSE')
    service.advance_clock(BASE+48*HOUR)
    assert len(service.list_notifications('user-a'))==1
    assert any(t['status']=='CANCELLED' for t in service.list_reminders('user-a'))


def test_unauthorized_action_has_no_effect(service):
    issue=pair(service)
    from app.service import DomainError
    with pytest.raises(DomainError):
        service.act('user-b',issue,{'event_id':'bad-close','action':'CLOSE'})
    assert service.get_issue('user-a',issue)['status']=='OPEN'


def test_refund_overpaid_and_missing_not_zero(service):
    service.receive('user-a','REFUND_ARRIVAL',arr('a1',11000))
    assert service.get_refund('user-a','r')['remaining_amount_fen'] is None
    service.receive('user-a','REFUND_CONFIRMED',conf())
    detail=service.get_refund('user-a','r')
    assert detail['overpaid_amount_fen']==1000
    assert detail['received_amount_fen']==11000


def test_business_conflicting_arrival_does_not_create_empty_refund(service):
    service.receive('user-a','REFUND_ARRIVAL',arr('a1'))
    result=service.receive('user-a','REFUND_ARRIVAL',arr('a1',refund='other',event_id='another'))
    assert result['result']=='CONFLICT'
    assert len(service.list_refunds('user-a'))==1

@pytest.mark.parametrize('bad_id',[{'not':'a-string'},['not-a-string']])
def test_invalid_event_id_is_retained_before_validation(service,bad_id):
    from app.service import DomainError
    from app.models import EventReceipt
    from sqlalchemy import select
    with pytest.raises(DomainError) as error:
        service.receive('user-a','TXN',tx('bad',event_id=bad_id))
    assert error.value.status_code==422
    with service.database.session_factory() as session:
        receipts=session.scalars(select(EventReceipt)).all()
        assert len(receipts)==1
        assert receipts[0].processing_state=='REJECTED'
        assert receipts[0].event_id is None

def test_repeat_close_preserves_confirmed_conclusion_on_reopen(service):
    issue=pair(service)
    action(service,issue,'CONFIRM_PROBLEM')
    service.act('user-a',issue,{'event_id':'close-1','action':'CLOSE'})
    service.advance_clock(BASE+1)
    service.act('user-a',issue,{'event_id':'close-2','action':'CLOSE'})
    action(service,issue,'REOPEN')
    result=service.get_issue('user-a',issue)
    assert result['status']=='CONFIRMED_PROBLEM'
    assert result['followup_mode']=='WAITING_FACT'