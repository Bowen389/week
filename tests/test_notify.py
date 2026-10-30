import copy
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import src.notify as notify

PAYLOAD = dict(rule='WEEK_262B_V1', signal_date='2026-10-30', data_source='tencent_https', signals=[
    dict(code='sz.002807', name='江阴银行', week_gain_pct=9, week_close=4.36)])

class NotifyTests(unittest.TestCase):
    def setup_files(self, tmp):
        root=Path(tmp)
        (root/'state').mkdir()
        (root/'state/account.json').write_text(json.dumps(dict(started='2026-10-27')))
        signal=root/'signals/2026-10-30/signals.json'
        signal.parent.mkdir(parents=True)
        signal.write_text(json.dumps(PAYLOAD))
        return root

    def test_acknowledged_signal_sent_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.setup_files(tmp)
            with patch.object(notify,'ROOT',root),patch.dict('os.environ',{'SERVERCHAN_SENDKEY':'test'},clear=True),patch.object(notify,'send',return_value='ack') as sender:
                notify.run();notify.run()
                self.assertEqual(sender.call_count,1)
                state=json.loads((root/'state/notifications.json').read_text())
                self.assertTrue(state['deliveries']['WEEK_262B_V1:2026-10-30']['sent'])

    def test_failure_preserves_retry_without_logging_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.setup_files(tmp)
            with patch.object(notify,'ROOT',root),patch.dict('os.environ',{'SENDKEY':'test'},clear=True),patch.object(notify,'send',side_effect=RuntimeError('secret-url')):
                with self.assertRaisesRegex(RuntimeError,'信号推送失败'):
                    notify.run()
            contents=(root/'state/notifications.json').read_text()
            self.assertNotIn('secret-url',contents)
            self.assertFalse(json.loads(contents)['deliveries']['WEEK_262B_V1:2026-10-30']['sent'])
            with patch.object(notify,'ROOT',root),patch.dict('os.environ',{'SENDKEY':'test'},clear=True),patch.object(notify,'send',return_value='ack') as sender:
                notify.run()
                self.assertEqual(sender.call_count,1)

    def test_missing_key_is_visible_and_does_not_mark_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.setup_files(tmp)
            with patch.object(notify,'ROOT',root),patch.dict('os.environ',{},clear=True):
                with self.assertRaisesRegex(RuntimeError,'未配置'):
                    notify.run()
            self.assertEqual(json.loads((root/'state/notifications.json').read_text())['deliveries'],{})

    def test_zero_signal_report_is_explicit(self):
        payload=copy.deepcopy(PAYLOAD);payload['signals']=[]
        title,body=notify.message(payload)
        self.assertIn('0只',title)
        self.assertIn('完整扫描无候选',body)
