import unittest
from decimal import Decimal
from unittest.mock import patch
from urllib.error import URLError

import daily_runner as runner
from stock_52week import HEADERS


class AlertTests(unittest.TestCase):
    def test_inclusive_threshold_and_failures_excluded(self):
        rows = [dict(zip(HEADERS, [symbol, symbol, 'A', 'risk', rate]))
                for symbol, rate in [('NO', '-29.99'), ('EDGE', '-30.00'),
                                     ('LOW', '-60.00'), ('FAIL', '')]]
        self.assertEqual([r[0] for r in runner.select_alerts(rows)], ['LOW', 'EDGE'])

    def test_message_splitting_preserves_three_fields(self):
        alerts = [(f'S{i}', '회사😀' * 10, Decimal('-30')) for i in range(300)]
        messages = runner.build_messages('us', alerts, '2026-10-24 16:00 KST')
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(m.encode('utf-16-le')) // 2 <= 3500 for m in messages))
        for symbol, name, rate in alerts:
            self.assertEqual(sum(f'{symbol} | {name} | {rate:.2f}%' in m for m in messages), 1)
        self.assertEqual(runner.build_messages('kr', [], 'now'), [])

    def test_transport_error_does_not_expose_token(self):
        token = 'test-secret-token'
        with patch.object(runner, 'urlopen', side_effect=URLError(f'https://api.telegram.org/bot{token}/sendMessage')):
            with self.assertRaises(RuntimeError) as captured:
                runner.send_message(token, 'chat', 'message')
        self.assertNotIn(token, str(captured.exception))


if __name__ == '__main__':
    unittest.main()
