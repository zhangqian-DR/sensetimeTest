"""通过真实HTTP接口重放十个验收场景；不修改数据库凑结果。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse
from uuid import uuid4

import httpx

HOUR = 3600000


class DemoError(RuntimeError):
    """外部请求或演示准备失败，必须作为失败展示。"""


class DemoSkip(RuntimeError):
    """当前运行方式无法核实的项目，明确标为未验证。"""


class DemoClient:
    """分离普通用户和演示管理员身份，不将认证信息写入证据。"""
    def __init__(self, base_url, user_token='demo-token-a', admin_token='local-demo-admin-only', transport=None):
        self.user_token = user_token
        self.admin_token = admin_token
        self.client = httpx.Client(base_url=base_url, timeout=15, transport=transport, trust_env=False)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.client.close()

    def request(self, method, path, payload=None, admin=False):
        token = self.admin_token if admin else self.user_token
        response = self.client.request(method, path, json=payload, headers={'Authorization': 'Bearer ' + token})
        if response.status_code >= 400:
            raise DemoError(f'{method} {path}: HTTP {response.status_code}: {response.text[:500]}')
        return response.json()

    def get(self, path, admin=False):
        return self.request('GET', path, admin=admin)

    def post(self, path, payload, admin=False):
        return self.request('POST', path, payload, admin=admin)

    def put(self, path, payload, admin=False):
        return self.request('PUT', path, payload, admin=admin)

    def now_ms(self):
        return self.get('/demo/clock', admin=True)['now_ms']

    def advance(self, hours=0):
        return self.post('/demo/clock/advance', {'hours': hours}, admin=True)['now_ms']


class ManagedServer:
    """仅管理本地演示子进程，使用临时库；重启时保留同一库。"""
    def __init__(self, base_url):
        parsed = urlparse(base_url)
        if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.path not in ('', '/') or parsed.username:
            raise DemoError('Managed server requires a local http://127.0.0.1 URL')
        self.base_url = base_url.rstrip('/')
        self.host = parsed.hostname
        self.port = parsed.port or 8000
        self.process = None
        self._temporary = None
        self._log = None
        self.database_path = None
        self.generation = 0

    def __enter__(self):
        self._temporary = Path(tempfile.mkdtemp(prefix='bill-followup-demo-'))
        self.log_path = self._temporary / 'server.log'
        self._log = self.log_path.open('ab')
        self.reset()
        return self

    def __exit__(self, *_):
        self.stop()
        if self._log:
            self._log.close()
        # 保留各场景的SQLite/WAL/SHM现场，报告给出路径，不自动删除演示证据。

    def stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            self.process = None

    def reset(self):
        self.stop()
        self.generation += 1
        self.database_path = self._temporary / f'scenario-{self.generation}.db'
        self.start()

    def restart(self):
        self.stop()
        self.start()

    def start(self):
        env = dict(os.environ)
        env.update(APP_TIME_MODE='simulated', APP_DB_PATH=str(self.database_path),
                   APP_CHECK_INTERVAL_SECONDS='3600', APP_ENABLE_DEMO_CONTROLS='true',
                   APP_SIMULATED_START='2026-10-28T14:00:00+08:00',
                   APP_TEST_TOKENS_JSON='{"demo-token-a":"user-a","demo-token-b":"user-b"}',
                   APP_DEMO_ADMIN_TOKEN='local-demo-admin-only', PYTHONUTF8='1')
        command = [sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', self.host, '--port', str(self.port), '--workers', '1', '--log-level', 'warning']
        # Windows不弹出额外控制台；服务输出写入本次演示日志。
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        self.process = subprocess.Popen(command, cwd=Path(__file__).resolve().parent, env=env,
                                        stdout=self._log, stderr=subprocess.STDOUT, creationflags=flags)
        deadline = time.monotonic() + 20
        with httpx.Client(timeout=0.5, trust_env=False) as probe:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    self._log.flush()
                    detail = self.log_path.read_text(encoding='utf-8', errors='replace')[-2000:]
                    raise DemoError('Managed server stopped before startup: ' + detail)
                try:
                    response = probe.get(self.base_url + '/health')
                    if response.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.05)
        self.stop()
        raise DemoError('Managed local server did not become healthy within 20 seconds')


class DemoRunner:
    """每个场景记录实际输入、断言和结果；失败绝不显示通过。"""
    NAMES = {
        1: '两笔相近交易形成一条疑似问题',
        2: '正常购买与交易/动作重放',
        3: '三笔交易形成三个明确关联的交易对',
        4: '退款逾期、分笔到账、去重及验证解决',
        5: '全额到账先于申请送达，不发过时提醒',
        6: '未回复按24/48小时最多追加两次',
        7: '明天提醒保留计数并至少延后24小时',
        8: '确认有问题仍待解决，停止无回复催办',
        9: '关闭后到账只补事实，不重开不催办',
        10: '真实进程重启保留处理结论与提醒次数',
    }

    def __init__(self, client, stream=None, managed_server=None):
        self.client = client
        self.stream = stream or sys.stdout
        self.managed_server = managed_server
        self.cases = {number: getattr(self, f'scenario_{number}') for number in range(1, 11)}
        self.prefix = uuid4().hex[:10]
        self.created_issues = []
        self.checks = []
        self.counter = 0

    def log(self, text):
        self.stream.write(str(text) + '\n')
        self.stream.flush()

    def check(self, condition, description):
        self.checks.append({'assertion': description, 'passed': bool(condition)})
        self.log(('  CHECK PASS: ' if condition else '  CHECK FAIL: ') + description)
        if not condition:
            raise AssertionError(description)

    def run(self, numbers):
        results = []
        for number in numbers:
            self.checks = []
            self.created_issues = []
            self.prefix = uuid4().hex[:10]
            name = self.NAMES.get(number, f'Scenario {number}')
            self.log(f'\n=== 场景 {number}: {name} ===')
            status, detail = 'PASS', '所有实际断言通过'
            try:
                if self.managed_server:
                    self.managed_server.reset()
                    self.log('  隔离：新的临时文件数据库和服务进程')
                self.cases[number]()
            except DemoSkip as error:
                status, detail = 'SKIP', str(error)
            except Exception as error:
                status, detail = 'FAIL', f'{type(error).__name__}: {error}'
            finally:
                if self.client and not self.managed_server:
                    self.cleanup()
            self.log(f'RESULT {number}: {status} — {detail}')
            results.append({'scenario': number, 'name': name, 'status': status, 'detail': detail, 'checks': list(self.checks)})
        report = {'passed': sum(x['status'] == 'PASS' for x in results),
                  'failed': sum(x['status'] == 'FAIL' for x in results),
                  'skipped': sum(x['status'] == 'SKIP' for x in results), 'results': results}
        self.log('\n汇总: ' + json.dumps({key: report[key] for key in ('passed', 'failed', 'skipped')}, ensure_ascii=False))
        return report

    def cleanup(self):
        # 外接服务只关闭本次创建的问题，不删除事实，不改他人/已有问题。
        for issue_id in self.created_issues:
            try:
                self.action(issue_id, 'CLOSE')
            except DemoError as error:
                self.log('  CLEANUP WARNING: ' + str(error))

    def identifier(self, suffix):
        self.counter += 1
        return f'{self.prefix}-{suffix}-{self.counter}'

    @staticmethod
    def iso(ms):
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(timezone(timedelta(hours=8))).isoformat(timespec='milliseconds')

    def event(self, endpoint, payload):
        self.log('  INPUT ' + endpoint + ' ' + json.dumps(payload, ensure_ascii=False))
        result = self.client.post('/events/' + endpoint, payload)
        self.log('  OBSERVED ' + json.dumps(result, ensure_ascii=False))
        return result

    def action(self, issue_id, action, **fields):
        payload = {'event_id': self.identifier('action'), 'action': action, **fields}
        self.log('  INPUT action ' + json.dumps(payload, ensure_ascii=False))
        result = self.client.post(f'/issues/{issue_id}/actions', payload)
        self.log('  OBSERVED ' + json.dumps(result, ensure_ascii=False))
        return result

    def issue(self, issue_id):
        return self.client.get('/issues/' + issue_id)

    def notifications(self, issue_id):
        tasks = [task for task in self.client.get('/reminders') if task.get('issue_id') == issue_id]
        task_ids = {task['id'] for task in tasks}
        return [message for message in self.client.get('/notifications') if message.get('task_id') in task_ids or message.get('issue_id') == issue_id]

    def advance(self, hours):
        before = self.client.now_ms()
        now = self.client.advance(hours)
        self.log(f'  TIME {self.iso(before)} -> {self.iso(now)} (+{hours}h)；由系统自动检查')
        return now

    def duplicate(self, offsets=(0, 5)):
        before = {item['id'] for item in self.client.get('/issues')}
        now = self.client.now_ms()
        merchant = self.identifier('shop')
        payloads = []
        for index, minute in enumerate(offsets):
            payload = {'event_id': self.identifier('txn-event'), 'tx_no': self.identifier('txn'), 'merchant_id': merchant,
                       'amount_fen': 3000, 'occurred_at': self.iso(now - HOUR + minute * 60000)}
            payloads.append(payload)
            self.event('transactions', payload)
        issues = [item for item in self.client.get('/issues') if item['id'] not in before]
        self.created_issues.extend(item['id'] for item in issues)
        return issues, payloads

    def refund_confirmation(self, refund_no):
        return self.event('refund-confirmations', {'event_id': self.identifier('confirm'), 'refund_no': refund_no,
                         'expected_amount_fen': 10000, 'confirmed_at': self.iso(self.client.now_ms())})

    def arrival(self, refund_no, amount, arrival_no=None):
        payload = {'event_id': self.identifier('arrival-event'), 'refund_no': refund_no,
                   'arrival_no': arrival_no or self.identifier('arrival'), 'amount_fen': amount,
                   'arrived_at': self.iso(self.client.now_ms())}
        self.event('refund-arrivals', payload)
        return payload

    def overdue_refund(self):
        before = {item['id'] for item in self.client.get('/issues')}
        refund_no = self.identifier('refund')
        self.refund_confirmation(refund_no)
        self.advance(72)
        issues = [item for item in self.client.get('/issues') if item['id'] not in before and item.get('issue_type') == 'REFUND_OVERDUE']
        self.check(len(issues) == 1, '满72小时未足额到账，只创建一条退款逾期问题')
        self.created_issues.append(issues[0]['id'])
        return refund_no, issues[0]['id']

    def scenario_1(self):
        issues, _ = self.duplicate()
        self.check(len(issues) == 1, '同商户同额相差5分钟，两笔形成一条疑似问题')
        detail = self.issue(issues[0]['id'])
        self.check(len(detail['transactions']) == 2, '原交易两笔均保留，并关联问题')
        self.check(detail['issue_type'] == 'DUPLICATE' and detail['status'] == 'OPEN', '问题为待确认疑似重复，不是已证实错误')

    def scenario_2(self):
        issues, payloads = self.duplicate()
        issue_id = issues[0]['id']
        payload = {'event_id': self.identifier('normal'), 'action': 'NORMAL'}
        for _ in range(2):
            self.client.post(f'/issues/{issue_id}/actions', payload)
        count = len(self.notifications(issue_id))
        for txn in payloads:
            self.event('transactions', txn)
        self.check(self.issue(issue_id)['status'] == 'NORMAL', '正常购买结论在动作和交易重放后保留')
        self.check(len(self.notifications(issue_id)) == count, '重放不重新发送提醒')

    def scenario_3(self):
        issues, payloads = self.duplicate((0, 5, 9))
        self.check(len(issues) == 3, '三笔两两满足，创建三个不同无序交易对')
        for item in issues:
            detail = self.issue(item['id'])
            self.check(len(detail['related_issue_ids']) == 2, '每条问题通过共享交易明确关联另两条')
        before = len(self.client.get('/issues'))
        for payload in payloads:
            self.event('transactions', payload)
        self.check(len(self.client.get('/issues')) == before, '三笔重放不新增问题')

    def scenario_4(self):
        refund_no, issue_id = self.overdue_refund()
        first = self.arrival(refund_no, 6000)
        self.check(self.issue(issue_id)['remaining_amount_fen'] == 4000, '应退100元、到账60元，仍差40元')
        self.event('refund-arrivals', first)
        self.check(self.issue(issue_id)['remaining_amount_fen'] == 4000, '重放60元到账不重复累计')
        second = self.arrival(refund_no, 4000)
        self.event('refund-arrivals', second)
        detail = self.issue(issue_id)
        self.check(detail['remaining_amount_fen'] == 0, '两笔到账合计100元，差额为0')
        self.check(detail['status'] == 'RESOLVED' and detail['resolution_basis'] == 'FULL_REFUND_VERIFIED', '依据全额到账事实验证解决')

    def scenario_5(self):
        refund_no = self.identifier('refund')
        before = len(self.client.get('/notifications'))
        self.arrival(refund_no, 10000)
        # 申请确认晚送达，但业务确认时间已经在72小时前。
        self.event('refund-confirmations', {'event_id': self.identifier('confirm'), 'refund_no': refund_no,
                   'expected_amount_fen': 10000, 'confirmed_at': self.iso(self.client.now_ms() - 73 * HOUR)})
        detail = self.client.get('/refunds/' + refund_no)
        self.check(detail['received_amount_fen'] == 10000 and detail['remaining_amount_fen'] == 0, '到账先到、补申请后准确归集')
        self.check(len(self.client.get('/notifications')) == before, '信息齐全已足额，不发送过时逾期消息')

    def scenario_6(self):
        issues, _ = self.duplicate()
        issue_id = issues[0]['id']
        self.check(self.issue(issue_id)['auto_followup_count'] == 0, '首次提醒不计追加次数')
        self.advance(23)
        self.check(self.issue(issue_id)['auto_followup_count'] == 0, '未满24小时不追加')
        self.advance(1)
        self.check(self.issue(issue_id)['auto_followup_count'] == 1, '满24小时第一次追加')
        self.advance(24)
        self.check(self.issue(issue_id)['auto_followup_count'] == 2, '满48小时第二次追加')
        self.advance(72)
        self.check(self.issue(issue_id)['auto_followup_count'] == 2 and len(self.notifications(issue_id)) == 3, '此后不再催办，总共三条成功提醒')

    def scenario_7(self):
        issues, _ = self.duplicate()
        issue_id = issues[0]['id']
        self.advance(24)
        before_count = self.issue(issue_id)['auto_followup_count']
        requested = self.client.now_ms()
        self.action(issue_id, 'SNOOZE', tomorrow=True)
        detail = self.issue(issue_id)
        self.check(detail['auto_followup_count'] == before_count, '延期不清零已有追加次数')
        self.check(detail['requested_remind_at'] >= requested + 24 * HOUR, '明天约定至少延后24小时')
        before_messages = len(self.notifications(issue_id))
        self.advance(23)
        self.check(len(self.notifications(issue_id)) == before_messages, '约定前不发送')
        self.advance(1)
        self.check(len(self.notifications(issue_id)) == before_messages + 1, '约定到期发送一次')
        self.check(self.issue(issue_id)['auto_followup_count'] == before_count, '用户约定不计自动追加且不重置')

    def scenario_8(self):
        issues, _ = self.duplicate()
        issue_id = issues[0]['id']
        self.action(issue_id, 'CONFIRM_PROBLEM')
        before = len(self.notifications(issue_id))
        self.advance(72)
        detail = self.issue(issue_id)
        self.check(detail['status'] == 'CONFIRMED_PROBLEM' and detail['resolution_basis'] is None, '确认有问题仍待解决，无成功结案依据')
        self.check(len(self.notifications(issue_id)) == before, '不沿用无回复催办')

    def scenario_9(self):
        refund_no, issue_id = self.overdue_refund()
        self.action(issue_id, 'CLOSE')
        before = len(self.notifications(issue_id))
        self.arrival(refund_no, 10000)
        self.advance(48)
        detail = self.issue(issue_id)
        self.check(detail['status'] == 'CLOSED' and detail['remaining_amount_fen'] == 0, '关闭结论保留，到账事实仍完整展示')
        self.check(len(self.notifications(issue_id)) == before, '关闭后不继续发送')

    def scenario_10(self):
        if not self.managed_server:
            raise DemoSkip('Real process restart not verified: use --managed-server')
        issues, payloads = self.duplicate()
        issue_id = issues[0]['id']
        self.advance(24)
        self.action(issue_id, 'CONFIRM_PROBLEM')
        before = self.issue(issue_id)
        count = len(self.notifications(issue_id))
        clock = self.client.now_ms()
        self.log('  RESTART 停止真实服务子进程，再以同一SQLite文件启动')
        self.managed_server.restart()
        after = self.issue(issue_id)
        self.check(after['status'] == before['status'] and after['auto_followup_count'] == before['auto_followup_count'], '真实重启保留用户确认结论和追加次数')
        self.check(self.client.now_ms() == clock and len(self.notifications(issue_id)) == count, '真实重启保留模拟时钟与实际已发消息')
        for payload in payloads:
            self.event('transactions', payload)
        self.check(len(self.notifications(issue_id)) == count, '重启后重放不重新发送')


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='账单跟进系统HTTP可重放验收，通知与数据均为模拟')
    parser.add_argument('--base-url', default=None)
    parser.add_argument('--port', type=int, default=8001)
    parser.add_argument('--scenario', choices=['all'] + [str(x) for x in range(1, 11)], default='all')
    parser.add_argument('--output', default='reports/demo_output.txt')
    parser.add_argument('--managed-server', action='store_true', help='管理本地子进程及临时数据库，支持真实重启验证')
    parser.add_argument('--user-token', default='demo-token-a')
    parser.add_argument('--admin-token', default='local-demo-admin-only')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    base_url = args.base_url or f'http://127.0.0.1:{args.port if args.managed_server else 8000}'
    numbers = list(range(1, 11)) if args.scenario == 'all' else [int(args.scenario)]
    try:
        with ExitStack() as stack:
            stream = stack.enter_context(output.open('w', encoding='utf-8'))
            server = stack.enter_context(ManagedServer(base_url)) if args.managed_server else None
            client = stack.enter_context(DemoClient(base_url, args.user_token, args.admin_token))
            report = DemoRunner(client, stream=stream, managed_server=server).run(numbers)
            if server:
                server._log.flush()
                output.with_suffix('.server.log').write_bytes(server.log_path.read_bytes())
            report['evidence'] = str(output)
            report['managed_server'] = args.managed_server
            report['database_directory'] = str(server._temporary) if server else None
            output.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"演示结果: {report['passed']} PASS, {report['failed']} FAIL, {report['skipped']} SKIP；证据 {output}")
        return 0 if report['failed'] == 0 and report['skipped'] == 0 else 1
    except Exception as error:
        detail = f'{type(error).__name__}: {error}'
        with output.open('a', encoding='utf-8') as stream:
            stream.write('\nDEMO SETUP FAILED: ' + detail + '\n')
        output.with_suffix('.json').write_text(json.dumps({'passed': 0, 'failed': 1, 'skipped': 0,
            'results': [], 'setup_error': detail}, ensure_ascii=False, indent=2), encoding='utf-8')
        print('演示失败，详情见 ' + str(output), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())