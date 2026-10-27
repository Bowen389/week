import unittest
from src.weekly import eligible, signal, weekly_candidate, entry_check, execute, STRATEGY
DAYS=['2026-10-19','2026-10-20','2026-10-21','2026-10-22','2026-10-23']
def candidate(code='sh.600001', close=10, gain=8):
    return dict(code=code,name='测试',strategy=STRATEGY,signal_date='2026-10-16',week_close=close,week_gain_pct=gain)
def history(last=10.8):
    return [dict(date='2026-10-16',open=10,high=10,low=10,close=10)]+[dict(date=d,open=10.5,low=10.5,high=last,close=last) for d in DAYS]
class Rules(unittest.TestCase):
    def test_universe(self):
        self.assertFalse(eligible('sh.000012','上证国债指数','2'))
        self.assertFalse(eligible('sz.002001','ST某某'))
        self.assertTrue(eligible('sh.603001','股票'))
    def test_complete_week(self):
        self.assertTrue(signal(history()[1:],DAYS))
        self.assertFalse(signal(history()[1:3],DAYS))
        self.assertFalse(signal(history()[1:3],DAYS[:2]))
    def test_previous_close_not_body(self):
        c=weekly_candidate(history(),DAYS)
        self.assertAlmostEqual(c['week_gain_pct'],8)
        self.assertLess(c['week_body_gain_pct'],4)
    def test_week_bounds(self):
        self.assertIsNone(weekly_candidate(history(10.4),DAYS))
        self.assertIsNotNone(weekly_candidate(history(11.2),DAYS))
        self.assertIsNone(weekly_candidate(history(11.21),DAYS))
        self.assertIsNone(weekly_candidate(history()[1:],DAYS))
    def test_gap_bounds_and_legacy(self):
        for price,expected in [(9.99,False),(10,True),(10.4,True),(10.401,False)]:
            self.assertEqual(entry_check(candidate(),dict(open=price))[0],expected)
        self.assertFalse(entry_check(dict(candidate(),strategy='old'),dict(open=10))[0])
        self.assertFalse(entry_check(candidate(),None)[0])
    def test_buy_plus_two(self):
        a=dict(cash=200000.,positions=[]);q={'sh.600001':dict(open=10,close=10,preclose=10)}
        execute(a,DAYS[0],DAYS,q,[candidate()])
        self.assertEqual(a['positions'][0]['exit_date'],DAYS[2])
        self.assertEqual(execute(a,DAYS[1],DAYS,q,[]),[])
        self.assertEqual(execute(a,DAYS[2],DAYS,q,[])[0]['action'],'SELL')
        self.assertEqual(a['positions'],[]);self.assertLess(a['cash'],200000)
    def test_filter_before_allocation(self):
        a=dict(cash=200000.,positions=[])
        q={'sh.600001':dict(open=10,close=10,preclose=10),'sh.600002':dict(open=9,close=9,preclose=10)}
        e=execute(a,DAYS[0],DAYS,q,[candidate(),candidate('sh.600002')])
        self.assertEqual(sum(x['action']=='BUY' for x in e),1)
        self.assertGreater(a['positions'][0]['cost'],190000)
    def test_limits_and_small_budget(self):
        a=dict(cash=200000.,positions=[]);q={'sh.600001':dict(open=10,close=10,preclose=9.09)}
        self.assertEqual(execute(a,DAYS[0],DAYS,q,[candidate()])[0]['reason'],'涨停开盘')
        q['sh.600001']['preclose']=10;execute(a,DAYS[0],DAYS,q,[candidate()]);q['sh.600001']['close']=9
        self.assertEqual(execute(a,DAYS[2],DAYS,q,[])[0]['action'],'SELL_DEFERRED')
        tiny=dict(cash=100.,positions=[])
        self.assertTrue(execute(tiny,DAYS[0],DAYS,q,[candidate()])[0]['entry_qualified'])
        self.assertEqual(tiny['positions'],[])
