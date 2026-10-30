"""Deliver persisted weekly signals; record acknowledgements separately from the ledger."""
import datetime as dt
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo
from .weekly import ROOT, save


def message(payload):
    signals = payload['signals']
    title = f"262B周线候选 {payload['signal_date']}：{len(signals)}只"
    lines = [f"信号日期：{payload['signal_date']}；数据源：{payload.get('data_source', 'baostock')}",
             '', '|代码|股票|周涨幅|周收盘|', '|---|---|---:|---:|']
    for item in signals:
        lines.append(f"|{item['code']}|{item['name']}|{item['week_gain_pct']:.2f}%|{item['week_close']:.2f}|")
    if not signals:
        lines.append('|—|本周完整扫描无候选|—|—|')
    lines += ['', '下一交易日开盘溢价在[0%,4%]且未涨停时才模拟买入；买入后第2个交易日收盘退出。',
              '20万元独立模拟仓，候选不是已成交记录。', '',
              f"[完整信号](https://github.com/Bowen389/week/tree/main/signals/{payload['signal_date']})"]
    return title, '\n'.join(lines)


def send(key, title, body):
    encoded = urllib.parse.urlencode({'title': title, 'desp': body}).encode()
    request = urllib.request.Request('https://sctapi.ftqq.com/' + key + '.send', data=encoded, method='POST')
    with urllib.request.urlopen(request, timeout=20) as response:
        reply = json.load(response)
    if reply.get('code') != 0:
        # Do not log the URL, token, or raw server response.
        raise RuntimeError(f"Server酱拒绝推送，错误码={reply.get('code', 'missing')}")
    return reply.get('data', {}).get('pushid')


def run():
    path = ROOT / 'state/notifications.json'
    status = json.loads(path.read_text()) if path.exists() else {'deliveries': {}}
    account = json.loads((ROOT / 'state/account.json').read_text())
    pending = []
    for file in sorted((ROOT / 'signals').glob('*/signals.json')):
        payload = json.loads(file.read_text())
        event = payload['rule'] + ':' + payload['signal_date']
        if payload['signal_date'] >= account['started'] and not status['deliveries'].get(event, {}).get('sent'):
            pending.append((event, payload))
    if not pending:
        print('NOTIFY_NO_PENDING: 已确认的信号不重复推送', flush=True)
        return
    key = (os.environ.get('SERVERCHAN_SENDKEY') or os.environ.get('SENDKEY') or '').strip()
    if not key:
        error = '未配置SERVERCHAN_SENDKEY或SENDKEY；信号已保存，配置后重跑可补发'
        status['last_error'] = error
        save(path, status)
        raise RuntimeError(error)
    for event, payload in pending:
        title, body = message(payload)
        try:
            pushid = send(key, title, body)
        except Exception as exc:
            # Exceptions from urllib may include the token-bearing URL.
            status['deliveries'][event] = {'sent': False, 'error_type': type(exc).__name__}
            save(path, status)
            raise RuntimeError('信号推送失败，账本保持不变；请检查Server酱额度或连接后重跑补发') from None
        status['deliveries'][event] = {'sent': True, 'pushid': pushid,
                                      'sent_at': dt.datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()}
        status.pop('last_error', None)
        save(path, status)
        print(f'NOTIFY_SENT {event}', flush=True)


if __name__ == '__main__':
    run()
