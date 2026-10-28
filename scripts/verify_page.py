"""可选浏览器验收：复用本机Edge，隔离数据库，不影响用户演示现场。"""
from pathlib import Path
import json
import re
import socket
import sys
from datetime import datetime, timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from demo import ManagedServer
from playwright.sync_api import sync_playwright, expect


def main():
    evidence=Path('reports'); evidence.mkdir(exist_ok=True)
    checks=[]; errors=[]
    def passed(name):
        checks.append({'check':name,'passed':True})
        print('PASS:',name)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0)); port=listener.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    with ManagedServer(base) as server, sync_playwright() as playwright:
        browser=playwright.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1050},locale='zh-CN')
        page.on('pageerror',lambda error:errors.append(str(error)))
        try:
            page.goto(base,wait_until='networkidle')
            expect(page.locator('#system-status')).to_contain_text('user-a')
            expect(page.locator('#stat-open')).to_have_text('0')
            passed('页面加载、当前用户和空数据正确')
            page.locator('[data-demo="pair"]').click()
            expect(page.locator('#issues-list .issue-card')).to_have_count(1)
            expect(page.locator('#detail-panel')).to_contain_text('疑似重复消费')
            expect(page.locator('#detail-panel tbody tr')).to_have_count(2)
            passed('页面真实创建交易对并展示两笔原交易')
            page.locator('[data-action="CONFIRM_PROBLEM"]').click()
            expect(page.locator('#detail-panel .badge.CONFIRMED_PROBLEM')).to_have_count(1)
            expect(page.locator('#detail-panel')).to_contain_text('仍待解决')
            page.locator('[data-action="SNOOZE"]').click()
            expect(page.locator('#detail-panel')).to_contain_text('用户约定时间')
            page.locator('[data-hours="24"]').click()
            expect(page.locator('#notifications-list .message-card')).to_have_count(2)
            passed('确认问题不结案，用户约定到期后真实发送提醒')
            page.select_option('#user-select','demo-token-b')
            expect(page.locator('#system-status')).to_contain_text('user-b')
            expect(page.locator('#stat-open')).to_have_text('0')
            expect(page.locator('#issues-list .issue-card')).to_have_count(0)
            expect(page.locator('#notifications-list .message-card')).to_have_count(0)
            passed('切换用户清空旧身份问题和消息')
            page.select_option('#user-select','demo-token-a')
            expect(page.locator('#system-status')).to_contain_text('user-a')
            expect(page.locator('#issues-list .issue-card')).to_have_count(1)
            page.locator('[data-demo="refund"]').click()
            expect(page.locator('#refund-no')).to_have_value(re.compile(r'page-.*-refund'))
            refund_no=page.locator('#refund-no').input_value()
            expect(page.locator('[data-hours="72"]')).to_be_enabled()
            page.locator('[data-hours="72"]').click()
            expect(page.locator('#issues-list .issue-card')).to_have_count(2)
            page.locator('#issues-list .issue-card').filter(has_text='退款逾期').click()
            expect(page.locator('#detail-panel .detail-grid')).to_contain_text('¥100.00')
            page.locator('[data-demo="arrival60"]').click()
            expect(page.locator('#detail-panel .detail-grid')).to_contain_text('¥40.00')
            page.locator('[data-demo="arrival40"]').click()
            expect(page.locator('#detail-panel .badge.RESOLVED')).to_have_count(1)
            expect(page.locator('#detail-panel')).to_contain_text('到账记录已验证足额')
            page.locator('[data-demo="replay"]').click()
            expect(page.locator('[data-demo="replay"]')).to_be_enabled()
            result=page.request.get(base+'/refunds/'+refund_no,headers={'Authorization':'Bearer demo-token-a'}).json()
            assert result['received_amount_fen']==10000 and result['remaining_amount_fen']==0
            passed('退款72小时、60/40到账、结案依据和原样重放均正确')
            page.screenshot(path=str(evidence/'page-desktop.png'),full_page=True)
            page.locator('#issues-list .issue-card').filter(has_text='疑似重复消费').click()
            page.once('dialog',lambda dialog:dialog.accept())
            page.locator('[data-action="CLOSE"]').click()
            expect(page.locator('#detail-panel .badge.CLOSED')).to_have_count(1)
            expect(page.locator('[data-action="REOPEN"]')).to_be_visible()
            passed('关闭有明确确认，保留历史并提供显式重开入口')
            page.locator('[data-paused="true"]').click()
            expect(page.locator('#detail-panel')).to_contain_text('当前用户已暂停')
            page.locator('[data-paused="false"]').click()
            expect(page.locator('#detail-panel')).not_to_contain_text('当前用户已暂停')
            passed('页面暂停与恢复通过真实接口生效')
            # 外部文字仅以文本呈现，不能通过HTML执行或越权。
            health=page.request.get(base+'/health').json()
            stamp=datetime.fromtimestamp(health['now_ms']/1000,timezone.utc).isoformat()
            malicious='<img src=x onerror="window.billXss=true">'
            for index in range(2):
                response=page.request.post(base+'/events/transactions',headers={'Authorization':'Bearer demo-token-a','Content-Type':'application/json'},data=json.dumps({
                    'event_id':f'xss-{index}','tx_no':f'xss-{index}','merchant_id':'xss-shop',
                    'merchant_name':malicious,'amount_fen':100,'occurred_at':stamp}))
                assert response.status==200
            page.locator('#refresh-btn').click()
            expect(page.locator('#issues-list')).to_contain_text(malicious)
            assert page.locator('#issues-list img').count()==0
            assert page.evaluate('window.billXss === true') is False
            passed('商户外部文字转义，不执行HTML')
            page.locator('.connection-settings > summary').click()
            page.fill('#user-token','invalid-demo-token')
            page.locator('#connect-btn').click()
            expect(page.locator('#error-banner')).to_be_visible()
            expect(page.locator('#stat-open')).to_have_text('—')
            passed('错误身份明确显示失败，不伪装成零条账目')
            page.fill('#user-token','demo-token-a')
            page.locator('#connect-btn').click()
            expect(page.locator('#system-status')).to_contain_text('user-a')
            page.locator('.connection-settings > summary').click()
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.screenshot(path=str(evidence/'page-mobile.png'),full_page=True)
            passed('390像素窄屏无整页横向溢出')
            assert not errors,errors
            passed('全程无浏览器脚本异常')
            report={'status':'PASS','checks':checks,'browser_errors':errors,'database_directory':str(server._temporary),
                    'time_utc':datetime.now(timezone.utc).isoformat(),'browser':'Microsoft Edge / Playwright','backend':'真实HTTP，独立模拟文件库'}
            (evidence/'page-browser.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        except Exception as error:
            page.screenshot(path=str(evidence/'page-browser-failure.png'),full_page=True)
            (evidence/'page-browser.json').write_text(json.dumps({'status':'FAIL','checks':checks,'error':str(error),'browser_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
            raise
        finally:
            browser.close()
    print('Browser acceptance complete; service stopped.')


if __name__=='__main__': main()