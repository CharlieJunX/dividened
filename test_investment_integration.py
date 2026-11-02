from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
import csv
import json
import pytest
from fundamentals.storage import Store
from fundamentals.report import deliver
from fundamentals.adapters.dart import parse_shares
from investment.report import run_market, save, notifications, build_messages, rank
from test_valuation import AS_OF, CONFIG, REVIEW, financials, prices
import dividend_report


def dividend():
    return {'market': 'us', 'code': 'SAMPLE', 'name': '가상기업', 'grade': 'A', 'current': 70,
            'currency': 'USD', 'candidate': True, 'drawdown': -30, 'as_of': str(AS_OF),
            'source': 'SYNTHETIC', 'metrics': {'status': '정상', 'yield_pct': 5, 'avg3_pct': 2, 'avg5_pct': 2,
              'ratio': 2.5, 'cagr_pct': 5, 'cut': False, 'ttm': 3.5, 'warnings': [], 'issues': [],
              'dividend_basis': 'SYNTHETIC ONLY'}}


def fundamental(status='NORMAL'):
    return {'code': 'SAMPLE', 'analysis': {'industry': 'GENERAL', 'status': status, 'latest_filing': '2026-08-14'},
            'facts': [f.dump() for f in financials()], 'review': REVIEW, 'drawdown': -30,
            'buy_candidate': True, 'decision': 'SYNTHETIC'}


def run(store, status='NORMAL', code='SAMPLE'):
    div, fin = dividend(), fundamental(status)
    div['code'], fin['code'] = code, code
    return run_market('us', [(code, '가상기업', 'A', '')], [], [div], [fin], {code: prices()},
                      CONFIG, dividend_report.load_config(Path('dividend_config.json')), store, AS_OF)


def test_rank_csv_bom_database_history_and_event_dedup(tmp_path):
    store = Store(tmp_path / 'state.sqlite3')
    first = run(store)
    assert first[0]['score']['grade'] == 'S'
    ordered = save(first, tmp_path)
    assert ordered[0]['overall_rank'] == ordered[0]['candidate_rank'] == 1
    assert (tmp_path / 'investment_all.csv').read_bytes().startswith(b'\xef\xbb\xbf')
    rows = list(csv.DictReader((tmp_path / 'investment_all.csv').open(encoding='utf-8-sig')))
    assert rows[0]['주주친화 등급'] == 'A' and rows[0]['종합 투자 등급'] == 'S'
    raw = (tmp_path / 'investment_all.csv').read_bytes()
    assert b'\r\r\n' not in raw
    csv_rows = list(csv.reader((tmp_path / 'investment_all.csv').open(encoding='utf-8-sig', newline='')))
    assert len(csv_rows) == 2 and all(len(r) == len(csv_rows[0]) for r in csv_rows)
    assert 'NORMAL' in '\n'.join(build_messages(first, CONFIG))
    assert notifications(store, first, AS_OF) == []
    high = run(store, 'HIGH_RISK')
    assert high[0]['score']['adjusted_score'] == 59
    assert set(high[0]['events']) >= {'SCORE_DROP', 'HIGH_RISK_ENTRY'}
    pending = notifications(store, high, AS_OF)
    assert pending
    sender = Mock()
    deliver(store, pending, sender)
    run(store, 'HIGH_RISK')
    assert notifications(store, high, AS_OF) == []
    assert store.db.execute('SELECT COUNT(*) FROM investment_history').fetchone()[0] == 3
    assert store.db.execute('SELECT COUNT(*) FROM financial_facts').fetchone()[0] > 0
    assert store.db.execute('SELECT COUNT(*) FROM valuation_months').fetchone()[0] == 60
    store.close()


def test_missing_data_and_rank_sort_do_not_invent_score(tmp_path):
    store = Store(tmp_path / 'state.sqlite3')
    rated = run(store)
    missing = run_market('eu', [('MISSING', '미지원 가상기업', '', '')], [], [], [], {}, CONFIG,
                         dividend_report.load_config(Path('dividend_config.json')), store, AS_OF)
    assert missing[0]['score']['adjusted_score'] is None and not missing[0]['score']['rank_eligible']
    ordered = rank(missing + rated)
    assert ordered[0]['code'] == 'SAMPLE' and ordered[1]['overall_rank'] is None
    save(ordered, tmp_path)
    assert 'N/A' in (tmp_path / 'investment_all.csv').read_text(encoding='utf-8-sig')
    store.close()


def test_failed_analysis_retains_previous_good_history(tmp_path):
    store = Store(tmp_path / 'state.sqlite3')
    run(store)
    with patch('investment.report.valuation_analysis', side_effect=[ValueError('SENSITIVE'),
              {'as_of': str(AS_OF), 'current': {'price': {'value': None}},
               'metrics': {}, 'samples': [], 'historically_cheap': False, 'band': 'N/A', 'warnings': [], 'signature': 'error'}]):
        failed = run(store)
    assert failed[0]['score']['adjusted_score'] is None
    assert failed[0]['failed']
    assert store.db.execute('SELECT COUNT(*) FROM investment_history').fetchone()[0] == 2
    values = [json.loads(row[0]) for row in store.db.execute('SELECT value FROM investment_history ORDER BY id')]
    assert values[0]['score']['grade'] == 'S' and values[1]['score']['grade'] is None
    assert 'SENSITIVE' not in json.dumps(values)
    store.close()


def test_dart_issued_common_shares_exclude_treasury_and_future_reports():
    row = {'se': '보통주', 'corp_code': '00000001', 'rcept_no': '20260814000001',
           'stlm_dt': '2026-06-30', 'distb_stock_co': '1,234', 'istc_totqy': '2,000', 'tesstk_co': '766'}
    fs = parse_shares({'list': [row]}, 2026, '11012', AS_OF, '00000001')
    assert fs[0].value == 1234 and fs[0].unit == 'shares' and fs[0].scope == 'issuer'
    row['rcept_no'] = '20270814000001'
    assert parse_shares({'list': [row]}, 2026, '11012', AS_OF, '00000001') == []


def test_cli_dry_run_reuses_history_and_saves_all_outputs(tmp_path):
    import daily_runner
    import stock_52week
    def quote(market, inputs, output):
        stock_52week.write_csv(output, stock_52week.HEADERS, [['SAMPLE', '가상기업', 'A', '', '-30']])
        output.with_suffix('.audit.json').write_text(json.dumps([{'code': 'SAMPLE', 'current': 70, 'high_52week': 100, 'currency': 'USD'}]))
        return 0
    def divs(*args, **kwargs):
        kwargs['history_sink']['SAMPLE'] = prices()
        return [dividend()], False
    with patch('sys.argv', ['daily_runner.py', '--market', 'us', '--dry-run', '--env-file', str(tmp_path / 'none'),
                            '--output-dir', str(tmp_path), '--state-dir', str(tmp_path / 'state')]), \
         patch.object(daily_runner.stocks, 'read_stocks', return_value=[('SAMPLE', '가상기업', 'A', '')]), \
         patch.object(daily_runner.stocks, 'run_market', side_effect=quote), \
         patch.object(daily_runner.dividends, 'run_market', side_effect=divs), \
         patch.object(daily_runner.fundamentals, 'run_market', return_value=([fundamental()], False)), \
         patch.object(daily_runner, 'fetch_yahoo_history') as fallback, \
         patch.object(daily_runner, 'send_message') as sender, \
         patch.object(daily_runner, 'datetime') as clock:
        clock.now.return_value = datetime(2026, 10, 28, 16, tzinfo=daily_runner.KST)
        assert daily_runner.main() == 0
    sender.assert_not_called()
    fallback.assert_not_called()
    assert (tmp_path / 'investment_all.csv').exists()
    assert (tmp_path / 'investment_candidates.csv').exists()
    assert (tmp_path / 'investment.audit.json').exists()
    assert not (tmp_path / 'state/fundamentals.sqlite3').exists()
    preview = (tmp_path / 'telegram_preview.txt').read_text(encoding='utf-8')
    assert '52주 최고가 대비' in preview and '[글로벌 주주친화 투자 분석]' in preview


def test_price_provider_mismatch_cannot_produce_high_score(tmp_path):
    store = Store(tmp_path / 'state.sqlite3')
    result = run_market('us', [('SAMPLE', '가상기업', 'A', '')],
        [{'code': 'SAMPLE', 'current': '7,000', 'currency': 'USD', 'high_52week': 10000}],
        [dividend()], [fundamental()], {'SAMPLE': prices()}, CONFIG,
        dividend_report.load_config(Path('dividend_config.json')), store, AS_OF)
    assert result[0]['failed'] and result[0]['score']['adjusted_score'] is None
    assert '현재가/이력 가격 불일치' in result[0]['issues']
    store.close()
