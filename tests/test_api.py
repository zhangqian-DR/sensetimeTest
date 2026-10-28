"""HTTP契约和生命周期测试；业务规则由真实存储测试另行验证。"""
from types import SimpleNamespace
import importlib
import threading
import time

from fastapi.testclient import TestClient
import pytest


class FakeDatabase:
    def __init__(self):
        self.initialized = False
        self.disposed = False
    def initialize(self):
        self.initialized = True
    def dispose(self):
        self.disposed = True


class FakeService:
    def __init__(self):
        self.calls = []
        self.recovered = False
        self.evaluated = threading.Event()
        self.now = 1793167200000
    def recover(self):
        self.recovered = True
    def evaluate(self):
        self.evaluated.set()
    def receive(self, user, kind, payload):
        self.calls.append((user, kind, payload))
        return {'result': 'APPLIED', 'receipt_id': 'receipt-1'}
    def act(self, user, issue, payload):
        return {'result': 'APPLIED', 'user_id': user, 'issue_id': issue}
    def set_paused(self, user, payload):
        return {'user_id': user, 'paused': payload['paused']}
    def list_issues(self, user):
        return [{'id': 'issue-a', 'user_id': user}]
    def get_issue(self, user, issue):
        if user != 'user-a':
            from fastapi import HTTPException
            raise HTTPException(404, 'Not found')
        return {'id': issue, 'user_id': user}
    def list_refunds(self, user):
        return []
    def get_refund(self, user, no):
        return {'refund_no': no}
    def list_reminders(self, user):
        return []
    def list_notifications(self, user):
        return []
    def get_event(self, user, receipt):
        return {'id': receipt}
    def history(self, user, issue):
        return []
    def advance_clock(self, target_ms):
        self.now = target_ms
        return target_ms
    def set_notification_behavior(self, behavior):
        self.behavior = behavior


def build_app(mode='simulated', controls=True):
    main = importlib.import_module('app.main')
    service = FakeService()
    service.clock = SimpleNamespace(now_ms=lambda: service.now)
    database = FakeDatabase()
    settings = SimpleNamespace(
        time_mode=mode, timezone='Asia/Shanghai',
        check_interval_seconds=0.01, test_tokens={'token-a': 'user-a', 'token-b': 'user-b'},
        demo_admin_token='admin', enable_demo_controls=controls,
    )
    return main.create_app(settings=settings, database=database, service=service), service, database


def auth(token='token-a'):
    return {'Authorization': f'Bearer {token}'}


def test_swagger_openapi_and_lifecycle():
    app, service, database = build_app()
    assert not database.initialized
    with TestClient(app) as client:
        assert database.initialized and service.recovered
        assert client.get('/docs').status_code == 200
        spec = client.get('/openapi.json').json()
        assert '/events/transactions' in spec['paths']
        assert 'BearerAuth' in spec['components']['securitySchemes']
        assert client.get('/health').json()['time_mode'] == 'simulated'
    assert database.disposed


def test_authenticated_payload_identity_is_not_overridden():
    app, service, _ = build_app()
    with TestClient(app) as client:
        assert client.get('/issues').status_code == 401
        assert client.get('/issues', headers=auth('wrong')).status_code == 401
        payload = {'event_id': 'x', 'user_id': 'user-b'}
        response = client.post('/events/transactions', headers=auth(), json=payload)
        assert response.status_code == 200
        assert service.calls[-1] == ('user-a', 'TXN', payload)
        assert client.get('/issues/issue-a', headers=auth('token-b')).status_code == 404


def test_receives_incomplete_business_payload_for_auditing():
    app, service, _ = build_app()
    with TestClient(app) as client:
        response = client.post('/events/refund-arrivals', headers=auth(), json={'event_id': 'bad'})
        assert response.status_code == 200
        assert service.calls[-1][1] == 'REFUND_ARRIVAL'
        assert service.calls[-1][2] == {'event_id': 'bad'}


def test_demo_requires_admin_and_advances_with_relative_hours():
    app, service, _ = build_app()
    with TestClient(app) as client:
        assert client.post('/demo/clock/advance', headers=auth(), json={'hours': 24}).status_code == 403
        before = service.now
        response = client.post('/demo/clock/advance', headers=auth('admin'), json={'hours': 24})
        assert response.status_code == 200
        assert service.now == before + 86400000
        assert response.json()['now_ms'] == service.now
        assert client.put('/demo/notification-behavior', headers=auth('admin'), json={'behavior': 'FAIL'}).status_code == 200
        assert service.behavior == 'FAIL'


def test_real_mode_disables_virtual_controls():
    app, _, _ = build_app(mode='real')
    with TestClient(app) as client:
        assert client.post('/demo/clock/advance', headers=auth('admin'), json={'hours': 24}).status_code == 403


def test_background_checks_without_user_requests():
    app, service, _ = build_app()
    with TestClient(app):
        assert service.evaluated.wait(1), 'background scheduler did not check'
    assert not app.state.scheduler.is_alive


def test_scheduler_retains_error_and_continues():
    module = importlib.import_module('app.scheduler')
    calls = []
    complete = threading.Event()
    def check():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('simulated check failure')
        complete.set()
    scheduler = module.BackgroundScheduler(check, 0.01)
    scheduler.start()
    assert complete.wait(1)
    scheduler.stop()
    assert 'simulated check failure' in scheduler.last_error
    assert scheduler.last_checked_at is not None
    assert not scheduler.is_alive


def test_clock_input_rejects_backwards_and_date_only():
    app, service, _ = build_app()
    with TestClient(app) as client:
        for payload in ({'hours': -1}, {'target': '2026-10-29'}, {'hours': True}, {'target': '2026-10-29T14:00:00'}):
            assert client.post('/demo/clock/advance', headers=auth('admin'), json=payload).status_code == 422

def test_swagger_documents_structured_actions_without_preempting_receipt():
    app, _, _ = build_app()
    with TestClient(app) as client:
        spec = client.get('/openapi.json').json()
        schema = spec['paths']['/issues/{issue_id}/actions']['post']['requestBody']['content']['application/json']['schema']
        actions = schema['properties']['action']['enum']
        assert set(actions) == {'NORMAL', 'CONFIRM_PROBLEM', 'SNOOZE', 'RESOLVE', 'CLOSE', 'REOPEN'}
        assert 'event_id' in schema['required']


def test_invalid_json_is_rejected_and_raw_input_is_retained_for_authenticated_user():
    app, service, _ = build_app()
    with TestClient(app) as client:
        response = client.post('/events/transactions', headers={**auth(), 'Content-Type': 'application/json'}, content='{bad json')
        assert response.status_code == 422
        assert service.calls[-1][0] == 'user-a'
        assert service.calls[-1][1] == 'INVALID_INPUT'
        assert service.calls[-1][2]['raw_body'] == '{bad json'


def test_domain_errors_map_to_http_status():
    app, service, _ = build_app()
    class DomainError(Exception):
        status_code = 409
        detail = 'conflicting input'
    def reject(*args):
        raise DomainError()
    service.receive = reject
    with TestClient(app) as client:
        response = client.post('/events/transactions', headers=auth(), json={'event_id': 'conflict'})
        assert response.status_code == 409
        assert response.json()['detail'] == 'conflicting input'

def test_invalid_json_does_not_bypass_authentication_or_create_user_audit():
    app, service, _ = build_app()
    with TestClient(app) as client:
        response = client.post('/events/transactions', headers={**auth('wrong'), 'Content-Type': 'application/json'}, content='{bad json')
        assert response.status_code == 401
        assert not service.calls

def test_demo_admin_changes_write_audit_without_tokens(capsys):
    app, _, _ = build_app()
    with TestClient(app) as client:
        client.post('/demo/clock/advance', headers=auth('admin'), json={'hours': 1})
        client.put('/demo/notification-behavior', headers=auth('admin'), json={'behavior': 'FAIL'})
    output = capsys.readouterr().err
    assert 'demo-clock' in output and 'before_ms=' in output
    assert 'demo-notification' in output and 'behavior=FAIL' in output
    assert 'Bearer' not in output

def test_database_error_reports_unknown_completion_without_exposing_sql():
    from sqlalchemy.exc import OperationalError
    app, service, _ = build_app()
    def unavailable(*args):
        raise OperationalError('private SQL statement', {}, RuntimeError('disk unavailable'))
    service.receive = unavailable
    with TestClient(app) as client:
        response = client.post('/events/transactions', headers=auth(), json={'event_id': 'original-event'})
        assert response.status_code == 503
        detail = response.json()['detail']
        assert 'event_id' in detail and '无法确认' in detail
        assert 'private SQL statement' not in detail
        assert '未收到' not in detail


def test_programming_error_is_not_misreported_as_database_failure():
    app, service, _ = build_app()
    def broken(*args):
        raise TypeError('programming error')
    service.receive = broken
    with TestClient(app) as client:
        with pytest.raises(TypeError, match='programming error'):
            client.post('/events/transactions', headers=auth(), json={'event_id': 'x'})