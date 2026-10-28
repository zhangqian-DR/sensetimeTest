"""HTTP演示基础测试，先于demo.py实现编写。"""
import importlib
import io
import json

import httpx
import pytest


def load_demo():
    return importlib.import_module('demo')


def test_client_uses_separate_admin_and_user_tokens():
    demo = load_demo()
    calls = []
    def respond(request):
        calls.append((request.url.path, request.headers['authorization']))
        if request.url.path == '/demo/clock':
            return httpx.Response(200, json={'now_ms': 1000, 'time_mode': 'simulated'})
        return httpx.Response(200, json=[])
    with demo.DemoClient('http://test', transport=httpx.MockTransport(respond)) as client:
        assert client.now_ms() == 1000
        assert client.get('/issues') == []
    assert calls == [('/demo/clock', 'Bearer local-demo-admin-only'), ('/issues', 'Bearer demo-token-a')]


def test_http_failure_is_failure_not_empty_success():
    demo = load_demo()
    transport = httpx.MockTransport(lambda _: httpx.Response(503, json={'detail': 'database unavailable'}))
    with demo.DemoClient('http://test', transport=transport) as client:
        with pytest.raises(demo.DemoError, match='503'):
            client.get('/issues')


def test_case_failure_is_reported_and_does_not_claim_pass():
    demo = load_demo()
    stream = io.StringIO()
    runner = demo.DemoRunner(None, stream=stream)
    def broken_case():
        raise AssertionError('expected amount 4000, got 0')
    runner.cases = {1: broken_case}
    report = runner.run([1])
    assert report['passed'] == 0
    assert report['failed'] == 1
    assert report['results'][0]['status'] == 'FAIL'
    assert 'expected amount 4000' in stream.getvalue()


def test_external_server_restart_is_explicitly_not_verified():
    demo = load_demo()
    runner = demo.DemoRunner(None, stream=io.StringIO())
    runner.cases = {10: runner.scenario_10}
    report = runner.run([10])
    assert report['skipped'] == 1
    assert report['results'][0]['status'] == 'SKIP'
    assert 'restart' in report['results'][0]['detail'].lower()


def test_evidence_contains_assertions_and_no_authorization_secrets():
    demo = load_demo()
    stream = io.StringIO()
    runner = demo.DemoRunner(None, stream=stream)
    runner.cases = {1: lambda: runner.check(4000 == 4000, 'remaining_amount_fen=4000')}
    report = runner.run([1])
    assert report['passed'] == 1
    assert 'remaining_amount_fen=4000' in stream.getvalue()
    assert 'Bearer' not in stream.getvalue()


def test_parse_cli_supports_managed_restart_and_single_scenario():
    demo = load_demo()
    args = demo.parse_args(['--managed-server', '--port', '8011', '--scenario', '10', '--output', 'out.txt'])
    assert args.managed_server is True
    assert args.port == 8011
    assert args.scenario == '10'


def test_managed_server_rejects_non_local_bind_target():
    demo = load_demo()
    with pytest.raises(demo.DemoError, match='local'):
        demo.ManagedServer('http://example.com:8001')

def test_all_ten_scenarios_through_managed_http_server(tmp_path):
    import socket
    demo = load_demo()
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    output = tmp_path / 'demo.txt'
    exit_code = demo.main(['--managed-server', '--port', str(port), '--output', str(output)])
    assert exit_code == 0
    report = json.loads(output.with_suffix('.json').read_text(encoding='utf-8'))
    assert report['passed'] == 10
    assert report['failed'] == report['skipped'] == 0
    assert report['results'][9]['status'] == 'PASS'


def test_managed_server_stop_releases_network_listener():
    import socket
    import httpx
    demo = load_demo()
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    with demo.ManagedServer(url) as server:
        assert httpx.get(url + '/health', trust_env=False).status_code == 200
        server.stop()
        with pytest.raises(httpx.TransportError):
            httpx.get(url + '/health', timeout=0.5, trust_env=False)

def test_managed_server_retains_scenario_database_for_forensic_review():
    import socket
    demo = load_demo()
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    with demo.ManagedServer(f'http://127.0.0.1:{port}') as server:
        database_path = server.database_path
    assert database_path.exists(), 'demo evidence database must remain available after the service stops'