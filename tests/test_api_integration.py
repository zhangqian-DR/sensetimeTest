"""真实HTTP集成：不替换业务服务，使用独立SQLite文件库。"""
from fastapi.testclient import TestClient


def configured_app(tmp_path):
    from app.config import load_settings
    from app.main import create_app
    settings = load_settings({'APP_DB_PATH': str(tmp_path / 'http.db'), 'APP_CHECK_INTERVAL_SECONDS': '60'})
    return create_app(settings=settings)


def headers(token='demo-token-a'):
    return {'Authorization': 'Bearer ' + token}


def txn(event, no, time):
    return {'event_id': event, 'tx_no': no, 'merchant_id': 'shop-1', 'amount_fen': 3000, 'occurred_at': time}


def test_real_http_duplicate_isolation_and_action_replay(tmp_path):
    with TestClient(configured_app(tmp_path)) as client:
        for payload in [txn('e1', 't1', '2026-10-28T09:00:00+08:00'), txn('e2', 't2', '2026-10-28T09:05:00+08:00')]:
            response = client.post('/events/transactions', headers=headers(), json=payload)
            assert response.status_code == 200, response.text
        issues = client.get('/issues', headers=headers()).json()
        assert len(issues) == 1
        issue_id = issues[0]['id']
        assert client.get('/issues/' + issue_id, headers=headers('demo-token-b')).status_code == 404
        normal = {'event_id': 'action-normal', 'action': 'NORMAL'}
        for _ in range(2):
            response = client.post('/issues/' + issue_id + '/actions', headers=headers(), json=normal)
            assert response.status_code == 200, response.text
        assert client.get('/issues/' + issue_id, headers=headers()).json()['status'] == 'NORMAL'
        # 另一用户的相同外部交易编号不应冲突。
        assert client.post('/events/transactions', headers=headers('demo-token-b'), json=txn('e1', 't1', '2026-10-28T09:00:00+08:00')).status_code == 200
        assert client.get('/issues', headers=headers('demo-token-b')).json() == []


def test_real_http_refund_clock_and_replay(tmp_path):
    with TestClient(configured_app(tmp_path)) as client:
        confirmation = {'event_id': 'r-confirm', 'refund_no': 'r1', 'expected_amount_fen': 10000, 'confirmed_at': '2026-10-28T14:00:00+08:00'}
        assert client.post('/events/refund-confirmations', headers=headers(), json=confirmation).status_code == 200
        assert client.post('/demo/clock/advance', headers=headers('local-demo-admin-only'), json={'hours': 72}).status_code == 200
        assert len(client.get('/issues', headers=headers()).json()) == 1
        for arrival_no, amount in [('a1', 6000), ('a2', 4000)]:
            arrival = {'event_id': arrival_no, 'arrival_no': arrival_no, 'refund_no': 'r1', 'amount_fen': amount, 'arrived_at': '2026-10-31T14:00:00+08:00'}
            for _ in range(2):
                response = client.post('/events/refund-arrivals', headers=headers(), json=arrival)
                assert response.status_code == 200, response.text
        issue = client.get('/issues', headers=headers()).json()[0]
        assert issue['status'] == 'RESOLVED'
        assert issue['resolution_basis'] == 'FULL_REFUND_VERIFIED'


def test_real_http_pause_defers_detection_until_resume(tmp_path):
    with TestClient(configured_app(tmp_path)) as client:
        assert client.put('/me/settings', headers=headers(), json={'event_id': 'pause', 'paused': True}).status_code == 200
        for payload in [txn('e1', 't1', '2026-10-28T09:00:00+08:00'), txn('e2', 't2', '2026-10-28T09:05:00+08:00')]:
            assert client.post('/events/transactions', headers=headers(), json=payload).status_code == 200
        assert client.get('/issues', headers=headers()).json() == []
        assert client.get('/notifications', headers=headers()).json() == []
        response = client.put('/me/settings', headers=headers(), json={'event_id': 'resume', 'paused': False})
        assert response.status_code == 200, response.text
        assert len(client.get('/issues', headers=headers()).json()) == 1

def test_invalid_optional_merchant_name_is_rejected_and_app_reopens(tmp_path):
    # 错误可选字段也必须留痕后返回422，不能毒化文件库导致恢复失败。
    with TestClient(configured_app(tmp_path)) as client:
        payload = txn('invalid-merchant', 't1', '2026-10-28T09:00:00+08:00')
        payload['merchant_name'] = {}
        response = client.post('/events/transactions', headers=headers(), json=payload)
        assert response.status_code == 422, response.text
    # 这里验证新应用/引擎重新初始化，真实进程重启另由demo场景10覆盖。
    with TestClient(configured_app(tmp_path)) as restarted:
        assert restarted.get('/health').status_code == 200
        assert restarted.get('/issues', headers=headers()).json() == []
        payload = txn('valid-merchant', 't1', '2026-10-28T09:00:00+08:00')
        payload['merchant_name'] = '模拟商户'
        assert restarted.post('/events/transactions', headers=headers(), json=payload).status_code == 200