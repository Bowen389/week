import unittest
from unittest.mock import patch
from src.backup import TencentSource

class Backup(unittest.TestCase):
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
