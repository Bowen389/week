import unittest
from src.weekly import eligible, signal, execute

class Rules(unittest.TestCase):
    def test_indices_excluded(self):
        self.assertFalse(eligible('sh.000012','上证国债指数','2'))
        self.assertFalse(eligible('sz.002001','ST某某'))
        self.assertTrue(eligible('sh.603001','股票'))
    def test_week(self):
        dates=['2026-10-19','2026-10-20','2026-10-21']
        bars=[dict(date=d,open=10,low=10,high=11,close=11) for d in dates]
        self.assertTrue(signal(bars,dates))
        self.assertFalse(signal(bars[:2],dates))
        self.assertFalse(signal(bars[:2],dates[:2]))
    def test_buy_plus_two_and_idempotence_boundary(self):
        a=dict(cash=200000.,positions=[])
        days=['2026-10-19','2026-10-20','2026-10-21','2026-10-22']
        q={'sh.600001':dict(open=10,close=10,preclose=10)}
        execute(a,days[0],days,q,[dict(code='sh.600001',name='测试')])
        self.assertEqual(a['positions'][0]['exit_date'],days[2])
        self.assertEqual(execute(a,days[1],days,q,[]),[])
        self.assertEqual(execute(a,days[2],days,q,[])[0]['action'],'SELL')
        self.assertEqual(len(a['positions']),0)
    def test_limits(self):
        a=dict(cash=200000.,positions=[])
        days=['2026-10-19','2026-10-20','2026-10-21','2026-10-22']
        s=[dict(code='sh.600001',name='测试')]
        self.assertEqual(execute(a,days[0],days,{'sh.600001':dict(open=11,close=11,preclose=10)},s)[0]['action'],'BUY_SKIPPED')
        execute(a,days[0],days,{'sh.600001':dict(open=10,close=10,preclose=10)},s)
        self.assertEqual(execute(a,days[2],days,{'sh.600001':dict(close=9,preclose=10)},[])[0]['action'],'SELL_DEFERRED')
