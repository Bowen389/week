import unittest
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from src.backup import TencentSource, Result
import src.weekly as weekly

class Backup(unittest.TestCase):
    def test_login_failure_switches_source(self):
        class Clock(dt.datetime):
            @classmethod
            def now(cls,tz=None):
                return cls(2026,10,29,19,tzinfo=tz)
        fallback=SimpleNamespace(
            query_trade_dates=lambda **k:Result([dict(calendar_date=d,is_trading_day='1') for d in ['2026-10-28','2026-10-29','2026-10-30','2026-11-02']]),
            query_stock_basic=lambda:Result([dict(code=f'sh.{600000+i}',code_name='测试',type='1',status='1') for i in range(1000)]),
            logout=lambda:None)
        failed=SimpleNamespace(login=lambda:SimpleNamespace(error_code='1',error_msg='offline'))
        with tempfile.TemporaryDirectory() as tmp, patch.object(weekly,'ROOT',Path(tmp)), patch.object(weekly.dt,'datetime',Clock), patch.dict(sys.modules,{'baostock':failed}), patch('src.backup.TencentSource',return_value=fallback):
            weekly.run()
            report=json.loads((Path(tmp)/'reports/latest.json').read_text())
            self.assertEqual(report['data_source'],'tencent_https')
            self.assertEqual(report['last_day'],'2026-10-29')

    def test_unadjusted_mapping_and_failed_download(self):
        source=TencentSource()
        bar=dict(date='2026-10-30',open='10',high='11',low='10',close='11',volume='200',previous_close='10')
        with patch('src.backup.tencent.history',return_value=[bar]):
            source.prefetch(['sh.600000'],'2026-10-01','2026-10-30')
        result=source.query_history_k_data_plus('sh.600000','',start_date='2026-10-30',end_date='2026-10-30')
        self.assertTrue(result.next())
        row=dict(zip(result.fields,result.get_row_data()))
        self.assertEqual(row['preclose'],'10')
        self.assertEqual(row['close'],'11')
        with patch('src.backup.tencent.history',side_effect=RuntimeError('offline')):
            source.prefetch(['sz.000001'],'2026-10-01','2026-10-30')
        with self.assertRaisesRegex(RuntimeError,'offline'):
            source.query_history_k_data_plus('sz.000001','',start_date='2026-10-30',end_date='2026-10-30')

    def test_stale_daily_bar_rejected(self):
        source=TencentSource()
        source.quotes={'sh.600000':dict(timestamp='20261030160000',volume='100',price='11')}
        with patch('src.backup.tencent.history',return_value=[]):
            source.prefetch(['sh.600000'],'2026-10-01','2026-10-30')
        with self.assertRaisesRegex(RuntimeError,'日线缺失'):
            source.query_history_k_data_plus('sh.600000','',start_date='2026-10-30',end_date='2026-10-30')
