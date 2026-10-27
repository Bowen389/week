"""Completed-week signals and end-of-day paper accounting. No broker orders."""
import argparse
import csv
import datetime as dt
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
PREFIXES = ('600', '601', '603', '605', '000', '001', '002', '003')


def eligible(code, name, kind='1'):
    return kind == '1' and code[:3] in ('sh.', 'sz.') and code[3:].startswith(PREFIXES) and not any(x in name.upper() for x in ('ST', '退'))


def week_key(day):
    return dt.date.fromisoformat(day).isocalendar()[:2]


def signal(bars, sessions):
    # Missing/suspended sessions cannot be silently treated as a complete week.
    if len(sessions) < 3 or [b['date'] for b in bars] != sessions:
        return False
    return (float(bars[-1]['close']) > float(bars[0]['open']) and
            abs(float(bars[0]['open']) - min(float(b['low']) for b in bars)) <= .010001 and
            abs(float(bars[-1]['close']) - max(float(b['high']) for b in bars)) <= .010001)


def limit_price(previous, ratio):
    return float((Decimal(str(previous)) * Decimal(str(ratio))).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def commission(value):
    return max(5., value * .0003)


def execute(account, day, sessions, quotes, pending):
    """Buy date=0, exit on exchange session +2; retry blocked exits."""
    events = []
    for p in list(account['positions']):
        b = quotes.get(p['code'])
        if day < p['exit_date'] or not b:
            continue
        if float(b['close']) <= limit_price(b['preclose'], .9) + .00001:
            events.append({'date': day, 'code': p['code'], 'action': 'SELL_DEFERRED', 'reason': '收盘跌停，不假定可成交'})
            continue
        value = p['shares'] * float(b['close'])
        fee = commission(value) + value * .0005
        account['cash'] += value - fee
        account['positions'].remove(p)
        events.append(dict(p, date=day, action='SELL', price=float(b['close']), fee=fee, pnl=value-fee-p['cost']))
    if pending:
        budget = min(account['cash'], 200000.) / len(pending)
        held = {p['code'] for p in account['positions']}
        for s in pending:
            b = quotes.get(s['code'])
            if not b or s['code'] in held or float(b['open']) >= limit_price(b['preclose'], 1.1) - .00001:
                events.append({'date': day, 'code': s['code'], 'action': 'BUY_SKIPPED', 'reason': '缺数据/停牌/已持有/涨停开盘'})
                continue
            price = float(b['open'])
            shares = int(budget / price / 100) * 100
            while shares and shares*price + commission(shares*price) > min(budget, account['cash']):
                shares -= 100
            if not shares:
                continue
            idx = sessions.index(day)
            if idx+2 >= len(sessions):
                raise RuntimeError('交易日历不足以确定退出日期')
            cost = shares*price + commission(shares*price)
            p = dict(code=s['code'], name=s['name'], buy_date=day, price=price, shares=shares, cost=cost, exit_date=sessions[idx+2])
            account['cash'] -= cost
            account['positions'].append(p)
            events.append(dict(p, date=day, action='BUY', fee=commission(shares*price)))
    return events


def rows(result):
    if result.error_code != '0':
        raise RuntimeError(result.error_msg)
    out = []
    while result.next():
        out.append(dict(zip(result.fields, result.get_row_data())))
    return out


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def run():
    import baostock as bs
    now = dt.datetime.now(ZoneInfo('Asia/Shanghai'))
    if now.hour < 18:
        raise RuntimeError('北京时间18点前不生成周收盘信号或盘后账本')
    state_path = ROOT/'state/account.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else dict(cash=200000., positions=[], processed=[], events=[], started=now.date().isoformat())
    login = bs.login()
    if login.error_code != '0':
        raise RuntimeError(login.error_msg)
    try:
        today = now.date().isoformat()
        start = (dt.date.fromisoformat(state.get('last_day', today))-dt.timedelta(days=14)).isoformat()
        calendar = rows(bs.query_trade_dates(start_date=start, end_date=f'{now.year+1}-01-31'))
        sessions = [r['calendar_date'] for r in calendar if r['is_trading_day']=='1']
        if not any(d > today for d in sessions):
            raise RuntimeError('日历缺少未来交易日，无法判断本周是否结束')
        due = [d for d in sessions if state.get('last_day', state['started']) <= d <= today and d not in state['processed']]
        for day in due:
            universe = rows(bs.query_stock_basic())
            universe = [r for r in universe if eligible(r['code'], r['code_name'], r['type']) and r['status']=='1']
            if len(universe) < 1000:
                raise RuntimeError('股票池不完整')
            idx=sessions.index(day)
            final = week_key(day) != week_key(sessions[idx+1])
            wk = [d for d in sessions if week_key(d)==week_key(day) and d<=day]
            prev = sessions[idx-1] if idx else None
            pending=[]
            if prev:
                f=ROOT/f'signals/{prev}/signals.json'
                if f.exists():
                    pending=json.loads(f.read_text())['signals']
            required={p['code'] for p in state['positions']} | {p['code'] for p in pending}
            scan = {r['code']:r['code_name'] for r in universe} if final else {}
            for p in state['positions']+pending:
                scan.setdefault(p['code'],p['name'])
            quotes={}; candidates=[]; failures=[]
            for code,name in sorted(scan.items()):
                try:
                    bars=rows(bs.query_history_k_data_plus(code, 'date,code,open,high,low,close,preclose,volume,tradestatus,isST', start_date=wk[0] if final else day, end_date=day, frequency='d', adjustflag='3'))
                    active=[b for b in bars if b['tradestatus']=='1' and b['isST']=='0' and float(b['volume'] or 0)>0]
                    if active and active[-1]['date']==day:
                        quotes[code]=active[-1]
                    if final and signal(active,wk):
                        candidates.append(dict(code=code,name=name,week_open=float(active[0]['open']),week_close=float(active[-1]['close']),week_gain_pct=(float(active[-1]['close'])/float(active[0]['open'])-1)*100))
                except Exception as exc:
                    failures.append(dict(code=code,error=str(exc)))
            if final and len(quotes) < 1000:
                failures.append(dict(code='MARKET', error='有效日线不足1000只，拒绝发布不完整信号'))
            if failures:
                save(ROOT/'reports/data_errors.json',dict(date=day,errors=failures))
                raise RuntimeError(f'{len(failures)}只数据读取失败；不冻结不完整信号，不推进账户日期')
            # All data validated before any account mutation.
            state['events'].extend(execute(state,day,sessions,quotes,pending))
            if final:
                payload=dict(signal_date=day,generated_at=now.isoformat(),signals=candidates,count=len(candidates),universe_count=len(universe),rule='weekly-solid-candle-v1')
                save(ROOT/f'signals/{day}/signals.json',payload)
                (ROOT/f'signals/{day}/README.md').write_text(f'# {day} 周线信号\n\n共 {len(candidates)} 只；次一交易日开盘模拟买入，买入后第2个交易日收盘退出。\n\n'+ '\n'.join(f"- {s['code']} {s['name']}：周涨幅 {s['week_gain_pct']:.2f}%" for s in candidates)+'\n',encoding='utf-8')
            state['processed'].append(day); state['last_day']=day
            save(state_path,state)
        summaries=[]
        for path in sorted((ROOT/'signals').glob('*/signals.json')):
            p=json.loads(path.read_text());summaries.append(dict(date=p['signal_date'],count=p['count']))
        save(ROOT/'reports/weekly_counts.json',summaries)
        (ROOT/'reports/weekly_counts.csv').write_text('week_end,count\n'+''.join(f"{p['date']},{p['count']}\n" for p in summaries),encoding='utf-8')
        save(ROOT/'reports/latest.json',dict(checked_at=now.isoformat(),cash=state['cash'],positions=state['positions'],last_day=state.get('last_day'),mode='EOD_DAILY_BAR_PAPER_NOT_LIVE',weekly_counts=summaries))
    finally:
        bs.logout()

if __name__=='__main__':
    run()
