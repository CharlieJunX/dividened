import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import alert


class DeliveryTests(unittest.TestCase):
    @patch("builtins.print")
    def test_send_baseline_catchup_retry(self, _print):
        releases = [alert.Release(str(i), "first quarter", str(2020+i), "", "") for i in range(4)]
        with tempfile.TemporaryDirectory() as tmp, patch.object(alert, "discover") as discover, \
             patch.object(alert, "message", side_effect=lambda r: r.key), patch.object(alert, "send") as send:
            state = Path(tmp) / "state.json"
            discover.return_value = releases[:2]
            send.return_value = 7
            alert.run("check", state)
            alert.run("check", state)
            self.assertEqual([c.args for c in send.call_args_list], [("1",)])
            discover.return_value = releases
            send.side_effect = [8, RuntimeError("delivery failed"), 9]
            with self.assertRaises(RuntimeError):
                alert.run("check", state)
            self.assertNotIn("3", json.loads(state.read_text()))
            alert.run("check", state)
            self.assertEqual(json.loads(state.read_text()), {"0":"baseline","1":7,"2":8,"3":9})


if __name__ == "__main__":
    unittest.main()
