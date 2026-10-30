"""Tencent HTTPS fallback with an exchange-maintained trading calendar."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from . import tencent

class Result:
    error_code = '0'
    error_msg = ''
    def __init__(self, records):
        self.records = records
        self.fields = list(records[0]) if records else []
        self.index = -1
    def next(self):
        self.index += 1
        return self.index < len(self.records)
    def get_row_data(self):
        return [self.records[self.index][k] for k in self.fields]

class TencentSource:
    def __init__(self):
        self.names = {}
        self.cache = {}
        self.quotes = {}
    def logout(self):
        pass
    def query_trade_dates(self, start_date, end_date):
        import exchange_calendars as xcals
        # XSHG publishes holidays one year at a time; use only known dates.
        known_end = f'{date.today().year}-12-31'
        cal = xcals.get_calendar('XSHG', start=start_date, end=min(end_date,known_end))
        return Result([dict(calendar_date=str(d.date()), is_trading_day='1') for d in cal.sessions])
    def query_stock_basic(self):
        ranks = tencent.rank_snapshot()
        quotes = tencent.quotes(list(ranks))
        self.quotes = {c[:2]+'.'+c[2:]: q for c,q in quotes.items()}
        self.names = {c[:2]+'.'+c[2:]: q['name'] for c,q in quotes.items()}
        return Result([dict(code=c,code_name=n,type='1',status='1') for c,n in self.names.items()])
    def prefetch(self, codes, start, end):
        def one(code):
            try:
                bars = tencent.history(code.replace('.', ''), 400)
                q = self.quotes.get(code)
                if q and q['timestamp'][:8] == end.replace('-','') and Decimal(q['volume']) > 0:
                    if not bars or bars[-1]['date'] != end or abs(Decimal(bars[-1]['close'])-Decimal(q['price'])) > Decimal('.01'):
                        raise tencent.DataError('当日日线缺失或与收盘快照不符 '+code)
                if not bars or bars[0]['date'] > start:
                    # Newly listed stocks may legitimately have less history.
                    if not bars:
                        raise tencent.DataError('日线为空 '+code)
                self.cache[code] = bars
            except Exception as exc:
                self.cache[code] = exc
        with ThreadPoolExecutor(max_workers=12) as pool:
            for i,_ in enumerate(pool.map(one,codes),1):
                if i % 200 == 0:
                    print(f'TENCENT_HISTORY {i}/{len(codes)}',flush=True)
    def query_history_k_data_plus(self, code, fields, start_date, end_date, **kwargs):
        bars = self.cache.get(code)
        if bars is None:
            self.prefetch([code],start_date,end_date)
            bars = self.cache[code]
        if isinstance(bars,Exception):
            raise bars
        out=[]
        for b in bars:
            if not start_date <= b['date'] <= end_date:
                continue
            out.append(dict(b,code=code,preclose=b['previous_close'] or '0',
                            tradestatus='1' if Decimal(b['volume'])>0 else '0',
                            isST='1' if 'ST' in self.names.get(code,'').upper() else '0'))
        return Result(out)
