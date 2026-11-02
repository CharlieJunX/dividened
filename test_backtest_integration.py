from copy import deepcopy
import csv
import json
from pathlib import Path
import pytest
import backtest_runner
from backtest import signals, portfolio
from backtest.report import save
from backtest.data import write_cache
from test_backtest import sample, signal, policies, record


def test_cli_offline_reproducible_reports_and_unavailable_not_zero(sample, tmp_path, monkeypatch):
    data, s, c = sample
    c.update(start='2020-01-01', end='2020-01-10', strategies=[{'id': 'score', 'drawdown_pct': -30, 'score_min': 70}],
             output_dir=str(tmp_path / 'reports'), data_file=str(tmp_path / 'input.json'))
    write_cache(c['data_file'], data)
    settings = tmp_path / 'config.json'
    settings.write_text(json.dumps(c), encoding='utf-8')
    # Any accidental notification or network path would fail this offline integration.
    import urllib.request
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: pytest.fail('Unexpected network call'))
    import stock_52week
    if hasattr(stock_52week, 'send_telegram'):
        monkeypatch.setattr(stock_52week, 'send_telegram', lambda *a, **k: pytest.fail('Unexpected Telegram call'))
    assert backtest_runner.main(['--config', str(settings), '--offline', '--mode', 'both']) == 0
    folder = next((tmp_path / 'reports').iterdir())
    result = json.loads((folder / 'summary.json').read_text(encoding='utf-8'))
    assert result['strategy_support'][0]['status'] == 'UNAVAILABLE'
    assert all(p['metrics']['total_return_pct'] is None for p in result['portfolio_summaries'])
    assert all(r['mean_net_pct'] is None for r in result['event_summaries'])
    assert result['data_sha256'] and result['code_sha256']
    for path in folder.glob('*.csv'):
        assert path.read_bytes().startswith(b'\xef\xbb\xbf')
        assert b'\r\r\n' not in path.read_bytes()
    assert 'N/A' in (folder / 'report.html').read_text(encoding='utf-8')
    assert (folder / 'input.json').exists() and (folder / 'telegram_preview.txt').exists()


def test_future_fact_and_same_day_correction_selected_by_publication_not_accession(sample, monkeypatch):
    _, s, c = sample
    c['strategies'] = [{'id': 'quality', 'drawdown_pct': -30, 'fundamental': True}]
    s['classifications'] = [record(industry='GENERAL')]
    fact = {'metric': 'net_income', 'value': 10, 'start': '2019-01-01', 'end': '2019-12-31',
            'filed': '2020-01-03', 'unit': 'KRW', 'scope': 'consolidated', 'standard': 'IFRS',
            'source': 'SYNTHETIC', 'accession': 'ZZZ', 'tag': 'net_income'}
    s['facts'] = [record(fact=fact, published_at='2020-01-06T09:00:00+09:00')]
    seen = []
    def mock_eval(facts, *args):
        seen.append(facts)
        return {'status': 'NORMAL', 'missing': [], 'issues': []}
    monkeypatch.setattr(signals, 'fundamentals_evaluate', mock_eval)
    signals.point(s, '2020-01-06', c, policies())
    assert seen[-1][0].value == 10
    s['facts'].append(record(fact={**fact, 'value': 20, 'accession': 'AAA'}, version='2',
                             published_at='2020-01-06T11:00:00+09:00'))
    signals.point(s, '2020-01-06', c, policies())
    assert len(seen[-1]) == 1 and seen[-1][0].value == 20
    s['facts'].append(record(fact={**fact, 'value': 99999}, version='3', published_at='2021-01-06T11:00:00+09:00'))
    signals.point(s, '2020-01-06', c, policies())
    assert seen[-1][0].value == 20


def test_score_reuses_existing_risk_cap_and_does_not_use_current_grade(sample, monkeypatch):
    _, s, c = sample
    c['strategies'] = [{'id': 'score', 'drawdown_pct': -30, 'score_min': 70}]
    s['classifications'] = [record(industry='GENERAL')]
    s['facts'] = [record(fact={'metric': 'net_income', 'value': 10, 'start': '2019-01-01', 'end': '2019-12-31',
                              'filed': '2020-01-03', 'unit': 'KRW', 'scope': 'consolidated', 'standard': 'IFRS',
                              'source': 'SYNTHETIC', 'accession': '1', 'tag': 'net_income'})]
    monkeypatch.setattr(signals, 'fundamentals_evaluate', lambda *a: {'status': 'HIGH_RISK'})
    pol = policies()
    monkeypatch.setattr(signals, 'value_analyze', lambda *a: {'valuation_coverage': 100, 'historically_cheap': True,
                         'metrics': {k: {'value': 1, 'percentile': 1 if k == 'fcf_yield' else 0}
                                     for k in pol['investment']['industries']['GENERAL']['valuation']}})
    monkeypatch.setattr(signals, 'growth_calculate', lambda *a: {k: {'score': w} for k, w in pol['investment']['industries']['GENERAL']['growth'].items()})
    monkeypatch.setattr(signals, 'risk_calculate', lambda *a: {k: {'score': 5} for k in ('volatility', 'mdd')})
    s['current_grade'] = 'A'
    assert signals.point(s, '2020-01-06', c, pol)['score'] is None
    s['grades'] = [record(grade='A')]
    result = signals.point(s, '2020-01-06', c, pol)
    assert result['score'] == 59 and result['score_audit']['risk_cap_applied']


def test_common_sample_does_not_promote_unavailable_strategy(sample):
    data, s, c = sample
    c.update(start='2020-01-01', end='2020-01-10', strategies=[{'id': 'price', 'drawdown_pct': -30},
                                                          {'id': 'score', 'drawdown_pct': -30, 'score_min': 70}])
    rows, events = signals.evaluate(data, c, policies())
    assert any(r['condition'] is not None for r in rows)
    assert not any(r['common_eligible'] for r in rows)


def test_historical_membership_not_inferred_from_current_list(sample):
    data, s, c = sample
    c.update(start='2020-01-01', end='2020-01-10', universe_mode='historical')
    rows, _ = signals.evaluate(data, c, policies())
    assert all(r['condition'] is None for r in rows)
    assert all(r['reason'] == 'HISTORICAL_MEMBERSHIP_NOT_CONFIRMED' for r in rows)


def test_failed_collection_preserves_reviewed_cache(sample, tmp_path, monkeypatch):
    from backtest import collection
    data, s, c = sample
    c.update(data_file=str(tmp_path / 'data.json'), collect=[{'symbol': 'TEST', 'market': 'kr'}])
    write_cache(c['data_file'], data)
    def fail(*args):
        raise OSError('unavailable')
    monkeypatch.setattr(collection, 'yahoo', fail)
    result, errors = collection.collect(c)
    assert result == data and errors[0]['cache_preserved'] is True
