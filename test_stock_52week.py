import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import stock_52week as app


class StockTests(unittest.TestCase):
    def test_sort_by_numeric_rate_with_failures_last(self):
        rows = [
            ["FAILED", "", "", "", ""],
            ["LESS", "", "", "", "-9.00"],
            ["MOST", "", "", "", "-50.00"],
            ["TIED", "", "", "", "-9.00"],
            ["HIGH", "", "", "", "0.00"],
        ]
        self.assertEqual([r[0] for r in app.sort_result_rows(rows)],
                         ["MOST", "LESS", "TIED", "HIGH", "FAILED"])

    def test_overseas_uses_regular_price_and_provider_52week_high(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = (
            b'{"chart":{"result":[{"meta":{"symbol":"SAP.DE","longName":"SAP",'
            b'"regularMarketPrice":80,"fiftyTwoWeekHigh":100,"regularMarketTime":1792769970,'
            b'"currency":"EUR"}}],"error":null}}')
        with patch.object(app, "urlopen", return_value=response):
            snapshot = app.overseas_quote("SAP.DE")
        self.assertEqual(app.rate(snapshot["current"], snapshot["high_52week"]), -20)
        self.assertEqual(snapshot["currency"], "EUR")

    def test_input_layouts_and_repeated_header(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "input.csv"
            path.write_text("Symbol,회사명,등급\nMUV2.DE,뮌헨재보험,A\nSymbol,회사명,등급\nSAP.DE,B\n", encoding="utf-8-sig")
            self.assertEqual(app.read_stocks(path, "eu"), [
                ("MUV2.DE", "뮌헨재보험", "A", ""), ("SAP.DE", "", "B", "")])
            path.write_text("종목코드,등급\n005930,B\n000270\n", encoding="cp949")
            self.assertEqual(app.read_stocks(path), [("005930", "", "B", ""), ("000270", "", "", "")])
            path.write_bytes('종목코드,회사명,등급,약점(리스크)\n005930,삼성전자,B,"수요 감소, 경쟁 심화\n규제 강화"\n'.encode("utf-8"))
            self.assertEqual(app.read_stocks(path), [("005930", "삼성전자", "B", "수요 감소, 경쟁 심화\n규제 강화")])
            path.write_text("5930,A", encoding="utf-8")
            with self.assertRaises(ValueError):
                app.read_stocks(path)

    def test_output_preserves_grade_name_and_failed_rows(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            output = Path(folder) / "result.csv"
            with patch.object(app, "get_quote", side_effect=[
                {"name": "Provider name", "current": 80, "high_52week": 100},
                ValueError("unknown symbol"),
            ]), patch.object(app.time, "sleep"):
                status = app.run_market("us", [("BAC", "한국어 회사명", "A", "신용손실, 규제"), ("INVALID", "실패 회사", "C", "조회 실패 리스크")], output)
            self.assertEqual(status, 1)
            with output.open(encoding="utf-8-sig", newline="") as stream:
                self.assertEqual(list(csv.reader(stream)), [app.HEADERS,
                    ["BAC", "한국어 회사명", "A", "신용손실, 규제", "-20.00"], ["INVALID", "실패 회사", "C", "조회 실패 리스크", ""]])

    def test_all_routes_every_market_even_on_failure(self):
        with patch("sys.argv", ["stock_52week.py", "all"]), patch.object(app, "read_stocks", return_value=[]), patch.object(app, "run_market", side_effect=[0, 1, 0]) as run:
            self.assertEqual(app.main(), 1)
            self.assertEqual([call.args[0] for call in run.call_args_list], ["kr", "us", "eu"])
        with patch("sys.argv", ["stock_52week.py", "--market", "eu"]), patch.object(app, "read_stocks", return_value=[]), patch.object(app, "run_market", return_value=0) as run:
            self.assertEqual(app.main(), 0)
            self.assertEqual(run.call_args.args[0], "eu")


if __name__ == "__main__":
    unittest.main()
