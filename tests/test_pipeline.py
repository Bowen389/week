import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import src.weekly as w
class Result:
    error_code='0';error_msg=''
    def __init__(self,rows):self.data=rows;self.fields=list(rows[0]) if rows else [];self.i=-1
    def next(self):self.i+=1;return self.i<len(self.data)
    def get_row_data(self):return [self.data[self.i][k] for k in self.fields]
class Clock(dt.datetime):
    day='2026-10-23'
    @classmethod
    def now(cls,tz=None):return cls.fromisoformat(cls.day+'T18:30:00').replace(tzinfo=tz)
class Pipeline(unittest.TestCase):
    def test_freeze_buy_sell_and_repeat(self):
        sessions=['2026-10-16','2026-10-19','2026-10-20','2026-10-21','2026-10-22','2026-10-23','2026-10-26','2026-10-27','2026-10-28','2026-10-29','2026-10-30','2026-11-02']
        def bar(d,code,price,op=None):
            return dict(date=d,code=code,open=str(op or price),high=str(price),low=str(op or price),close=str(price),preclose='10',volume='10000',tradestatus='1',isST='0')
        def hist(code,fields,start_date,end_date,**kwargs):
            if end_date=='2026-10-23':
                close=10.8 if code=='sh.600000' else 10.2
                return Result([bar('2026-10-16',code,10)]+[bar(d,code,close,10.1) for d in sessions[1:6]])
            return Result([bar(end_date,code,11,10.8)])
        bs=SimpleNamespace(login=lambda:SimpleNamespace(error_code='0',error_msg=''),logout=lambda:None,
            query_trade_dates=lambda **k:Result([dict(calendar_date=d,is_trading_day='1') for d in sessions]),
            query_stock_basic=lambda:Result([dict(code=f'sh.{600000+i}',code_name='测试',type='1',status='1') for i in range(1000)]),query_history_k_data_plus=hist)
        with tempfile.TemporaryDirectory() as tmp,patch.object(w,'ROOT',Path(tmp)),patch.object(w.dt,'datetime',Clock),patch.dict(sys.modules,{'baostock':bs}):
            Clock.day='2026-10-23';w.run();w.run()
            frozen=json.loads((Path(tmp)/'signals/2026-10-23/signals.json').read_text())
            self.assertEqual(frozen['count'],1);self.assertEqual(frozen['rule'],w.STRATEGY)
            Clock.day='2026-10-26';w.run();w.run()
            state=json.loads((Path(tmp)/'state/account.json').read_text())
            self.assertEqual(len(state['positions']),1);self.assertEqual(state['positions'][0]['exit_date'],'2026-10-28')
            Clock.day='2026-10-28';w.run();w.run()
            state=json.loads((Path(tmp)/'state/account.json').read_text())
            self.assertEqual(state['positions'],[]);self.assertEqual([e['action'] for e in state['events']],['BUY','SELL'])
            counts=json.loads((Path(tmp)/'reports/weekly_counts.json').read_text())
            self.assertEqual(counts[0]['opening_matches'],1);self.assertEqual(counts[0]['buys'],1)
