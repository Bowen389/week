"""Completed-week signals and end-of-day paper accounting. No broker orders."""
import datetime as dt
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
PREFIXES = ('600', '601', '603', '605', '000', '001', '002', '003')
STRATEGY = 'WEEK_262B_V1'
RULE = dict(strategy=STRATEGY, week_gain_min_exclusive=4, week_gain_max_inclusive=12,
            opening_gap_min_inclusive=0, opening_gap_max_inclusive=4, capital=200000, exit_sessions=2)


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


def weekly_candidate(bars, sessions):
    """262B gain uses the last traded close BEFORE the signal week."""
    current = [b for b in bars if b['date'] in sessions]
    prior = [b for b in bars if b['date'] < sessions[0]]
    if not signal(current, sessions) or not prior:
        return None
    previous = Decimal(str(prior[-1]['close']))
    if previous <= 0:
        return None
    close = Decimal(str(current[-1]['close']))
    gain = (close / previous - 1) * 100
    if not Decimal('4') < gain <= Decimal('12'):
        return None
    return dict(strategy=STRATEGY, signal_date=sessions[-1],
                previous_week_close=float(previous), week_open=float(current[0]['open']),
                week_close=float(close), week_gain_pct=float(gain),
                week_body_gain_pct=float((close / Decimal(str(current[0]['open'])) - 1) * 100))


def entry_check(candidate, quote):
    # Reject legacy archives with the old, week-open-based gain definition.
    if candidate.get('strategy') != STRATEGY:
        return False, None, '候选版本不是262B'
    if not Decimal('4') < Decimal(str(candidate.get('week_gain_pct', 0))) <= Decimal('12'):
        return False, None, '周涨幅不在(4%,12%]'
    reference = Decimal(str(candidate.get('week_close', 0)))
    if reference <= 0 or not quote or float(quote.get('open', 0)) <= 0:
        return False, None, '缺少有效开盘价或信号周收盘价'
    gap = (Decimal(str(quote['open'])) / reference - 1) * 100
    return Decimal('0') <= gap <= Decimal('4'), float(gap), '开盘溢价不在[0%,4%]'


def limit_price(previous, ratio):
    return float((Decimal(str(previous)) * Decimal(str(ratio))).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def commission(value):
    return max(5., value * .0003)


def execute(account, day, sessions, quotes, pending):
    """Buy date=0, exit at session +2 close. Never spend same-day close proceeds at open."""
    events = []
    opening_cash = account['cash']
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
    held_at_open = {p['code'] for p in account['positions']} | {e['code'] for e in events if e['action']=='SELL'}
    qualified = []
    for candidate in pending:
        b = quotes.get(candidate['code'])
        ok, gap, reason = entry_check(candidate, b)
        if ok and candidate['code'] in held_at_open:
            ok, reason = False, '开盘时已持有'
        if ok and float(b['open']) >= limit_price(b['preclose'], 1.1) - .00001:
            ok, reason = False, '涨停开盘'
        details = dict(date=day, code=candidate['code'], strategy=STRATEGY,
                       signal_date=candidate.get('signal_date'), opening_gap_pct=gap)
        if not ok:
            events.append(dict(details, action='BUY_SKIPPED', entry_qualified=False, reason=reason))
        else:
            qualified.append((candidate, b, details))
    budget = min(opening_cash, RULE['capital']) / len(qualified) if qualified else 0
    for candidate, b, details in qualified:
        price = float(b['open'])
        shares = int(budget / price / 100) * 100
        while shares and shares*price + commission(shares*price) > min(budget, account['cash']):
            shares -= 100
        if not shares:
            events.append(dict(details, action='BUY_SKIPPED', entry_qualified=True, reason='分配资金不足一手及费用'))
            continue
        idx = sessions.index(day)
        if idx+2 >= len(sessions):
            raise RuntimeError('交易日历不足以确定退出日期')
        cost = shares*price + commission(shares*price)
        position = dict(code=candidate['code'], name=candidate['name'], strategy=STRATEGY,
                        signal_date=candidate['signal_date'], opening_gap_pct=details['opening_gap_pct'],
                        buy_date=day, price=price, shares=shares, cost=cost, exit_date=sessions[idx+2])
        account['cash'] -= cost
        account['positions'].append(position)
        events.append(dict(position, date=day, action='BUY', entry_qualified=True, fee=commission(shares*price)))
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
            scan = {r['code']:r['code_name'] for r in universe} if final else {}
            for p in state['positions']+pending:
                scan.setdefault(p['code'],p['name'])
            quotes={}; candidates=[]; failures=[]
            # Include enough prior history to find the last traded close even after suspension.
            history_start = (dt.date.fromisoformat(wk[0])-dt.timedelta(days=60)).isoformat() if final else day
            for code,name in sorted(scan.items()):
                try:
                    bars=rows(bs.query_history_k_data_plus(code, 'date,code,open,high,low,close,preclose,volume,tradestatus,isST', start_date=history_start, end_date=day, frequency='d', adjustflag='3'))
                    active=[b for b in bars if b['tradestatus']=='1' and b['isST']=='0' and float(b['volume'] or 0)>0]
                    if active and active[-1]['date']==day:
                        quotes[code]=active[-1]
                    if final:
                        metrics = weekly_candidate(active, wk)
                        if metrics:
                            candidates.append(dict(code=code, name=name, **metrics))
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
                payload=dict(signal_date=day,generated_at=now.isoformat(),signals=candidates,count=len(candidates),universe_count=len(universe),rule=STRATEGY, parameters=RULE)
                save(ROOT/f'signals/{day}/signals.json',payload)
                (ROOT/f'signals/{day}/README.md').write_text(f'# {day} 262B周线候选\n\n共 {len(candidates)} 只；周涨幅(4%,12%]。下一交易日仅开盘溢价[0%,4%]时模拟买入，买入后第2个交易日收盘退出。\n\n'+ '\n'.join(f"- {s['code']} {s['name']}：周涨幅 {s['week_gain_pct']:.2f}%" for s in candidates)+'\n',encoding='utf-8')
            state['strategy'] = STRATEGY
            state['processed'].append(day); state['last_day']=day
            save(state_path,state)
        summaries=[]
        for path in sorted((ROOT/'signals').glob('*/signals.json')):
            p=json.loads(path.read_text())
            events=[e for e in state['events'] if e.get('signal_date')==p['signal_date'] and e['action'] in ('BUY', 'BUY_SKIPPED')]
            summaries.append(dict(date=p['signal_date'],count=p['count'],strategy=p['rule'],
                                  opening_matches=sum(bool(e.get('entry_qualified')) for e in events),
                                  buys=sum(e['action']=='BUY' for e in events)))
        save(ROOT/'reports/weekly_counts.json',summaries)
        (ROOT/'reports/weekly_counts.csv').write_text('week_end,count,strategy,opening_matches,buys\n'+''.join(f"{p['date']},{p['count']},{p['strategy']},{p['opening_matches']},{p['buys']}\n" for p in summaries),encoding='utf-8')
        save(ROOT/'reports/latest.json',dict(strategy=STRATEGY,parameters=RULE,checked_at=now.isoformat(),cash=state['cash'],positions=state['positions'],last_day=state.get('last_day'),mode='EOD_DAILY_BAR_PAPER_NOT_LIVE',weekly_counts=summaries))
    finally:
        bs.logout()

if __name__=='__main__':
    run()
