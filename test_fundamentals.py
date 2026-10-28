"""Deterministic fixtures; no live provider or Telegram traffic."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
import json
from unittest.mock import Mock, patch
import pytest
from fundamentals.config import load_config
from fundamentals.models import Fact
from fundamentals.periods import growth, quarters, ttm_pair, versions
from fundamentals.evaluation import evaluate, event
from fundamentals.classification import classify
from fundamentals.storage import Store
from fundamentals.report import run_market, notifications, deliver, message
from fundamentals.adapters.sec import parse_facts
from fundamentals.adapters.sec import SEC
from fundamentals.adapters.dart import DART
from fundamentals.adapters.dart import parse_statement, amount
from fundamentals.adapters.europe import Europe
from fundamentals.http import Client
from fundamental_state_sync import sync

AS_OF = date(2026, 10, 27)
CONFIG = load_config()


def fact(metric, value, start, end, **kwargs):
    defaults = dict(filed=(date.fromisoformat(end) + timedelta(days=25)).isoformat(),
                    unit='USD/shares' if metric in {'eps', 'dps'} else 'USD',
                    scope='consolidated', standard='us-gaap', source='https://official.example/filing',
                    accession=end, tag=metric, eps_verified=metric == 'eps')
    defaults.update(kwargs)
    return Fact(metric, value, start, end, **defaults)


def sample():
    facts = []
    for year in range(2020, 2026):
        for metric, value in {'revenue': 400, 'operating_income': 100, 'net_income': 80,
                              'ocf': 100, 'capex': 20, 'dividends': 10, 'dps': 1}.items():
            facts.append(fact(metric, value, f'{year}-01-01', f'{year}-12-31'))
    for year, quarter in [(2024, 4), (2025, 1), (2025, 2), (2025, 3), (2025, 4), (2026, 1), (2026, 2), (2026, 3)]:
        ends = {1: '03-31', 2: '06-30', 3: '09-30', 4: '12-31'}
        starts = {1: '01-01', 2: '04-01', 3: '07-01', 4: '10-01'}
        for metric, value in {'revenue': 100, 'operating_income': 25, 'net_income': 20,
                              'eps': 1, 'ebitda': 50, 'interest': 5}.items():
            facts.append(fact(metric, value, f'{year}-{starts[quarter]}', f'{year}-{ends[quarter]}'))
    for year in range(2019, 2026):
        facts.append(fact('equity', 200, None, f'{year}-12-31'))
    facts += [fact('cash', 100, None, '2026-09-30'), fact('total_debt', 200, None, '2026-09-30')]
    return facts


def assessed(facts=None, industry='GENERAL', config=None):
    return evaluate(facts if facts is not None else sample(), AS_OF, industry, config or CONFIG)


def edit(facts, metric, value, predicate=lambda f: True):
    return [replace(f, value=value) if f.metric == metric and predicate(f) else f for f in facts]


def test_complete_general_normal_and_auditable_ratios():
    r = assessed()
    assert r['status'] == 'NORMAL', r['missing']
    assert r['metrics']['revenue_growth']['value'] == 0
    assert r['metrics']['net_debt_ebitda']['value'] == .5
    assert r['metrics']['fcf']['value'] == 80
    assert r['metrics']['dividend_fcf']['value'] == .125
    assert r['metrics']['revenue_growth']['evidence'][0]['filed']


@pytest.mark.parametrize('current,prior,expected', [(90, 100, -10), (120, 100, 20), (1, 0, None), (1, -1, None)])
def test_growth(current, prior, expected):
    actual = growth(current, prior)
    assert actual is None if expected is None else actual == pytest.approx(expected)


def test_cumulative_quarters_and_q4_not_double_counted():
    values = [('2024-03-31', 100), ('2024-06-30', 230), ('2024-09-30', 360), ('2024-12-31', 500)]
    fs = [fact('revenue', value, '2024-01-01', end) for end, value in values]
    assert [f.value for f in quarters(fs)] == [100, 130, 130, 140]
    assert ttm_pair(fs) is None
    fs += [fact('revenue', value, '2025-01-01', end.replace('2024', '2025')) for end, value in values]
    assert ttm_pair(fs)[:2] == (500, 500)


def test_eps_cumulative_never_subtracted_and_unverified_never_summed():
    assert len(quarters([fact('eps', 3, '2025-01-01', '2025-09-30')])) == 0
    fs = [replace(f, eps_verified=False) if f.metric == 'eps' else f for f in sample()]
    r = assessed(fs)
    assert r['status'] == 'INSUFFICIENT_DATA'
    assert r['metrics']['eps_growth']['state'] == 'N/A'


def test_profit_to_loss_and_nonpositive_base():
    fs = edit(sample(), 'operating_income', -10, lambda f: f.start >= '2025-10-01')
    r = assessed(fs)
    assert r['metrics']['operating_income_growth']['state'] == 'SEVERE'
    assert r['status'] == 'WARNING'
    fs = edit(fs, 'operating_income', -5)
    r = assessed(fs)
    assert r['metrics']['operating_income_growth']['value'] is None
    assert r['metrics']['operating_income_growth']['reason'] == '적자 지속 또는 0 이하'
    assert 'operating_income_growth' not in r['missing']


def test_two_year_negative_ocf_fcf_high_risk():
    fs = edit(sample(), 'ocf', -10, lambda f: f.end >= '2024-12-31')
    r = assessed(fs)
    assert r['metrics']['ocf']['state'] == 'SEVERE'
    assert r['metrics']['fcf']['state'] == 'SEVERE'
    assert r['status'] == 'HIGH_RISK'


@pytest.mark.parametrize('ebitda', [0, -10])
def test_nonpositive_ebitda_has_no_multiple(ebitda):
    r = assessed(edit(sample(), 'ebitda', ebitda))
    assert r['metrics']['net_debt_ebitda']['value'] is None
    assert '0 이하' in r['metrics']['net_debt_ebitda']['reason']
    assert r['status'] == 'INSUFFICIENT_DATA'


@pytest.mark.parametrize('metric', ['revenue', 'eps', 'ocf', 'capex', 'total_debt', 'dividends'])
def test_missing_essential_never_normal(metric):
    assert assessed([f for f in sample() if f.metric != metric])['status'] == 'INSUFFICIENT_DATA'


def test_missing_debt_components_not_zero_and_negative_capex_not_abs():
    fs = [f for f in sample() if f.metric != 'total_debt']
    fs += [fact('short_debt', 10, None, '2026-09-30')]
    assert assessed(fs)['metrics']['net_debt']['value'] is None
    assert assessed(edit(sample(), 'capex', -20))['metrics']['fcf']['value'] is None


def test_income_nonpositive_payout_and_cash_conversion_na():
    r = assessed(edit(sample(), 'net_income', -1))
    assert r['metrics']['ocf_income']['value'] is None
    assert r['metrics']['dividend_income']['value'] is None


def test_financial_policies_do_not_apply_general_cashflow_signals():
    fs = edit(sample(), 'ocf', -100)
    for kind in ['BANK', 'INSURER', 'OTHER_FINANCIAL']:
        r = assessed(fs, kind)
        assert 'fcf' not in r['metrics'] and 'net_debt_ebitda' not in r['metrics']
        assert r['metrics']['roe']['value'] == 40
    r = assessed(fs, 'REIT')
    assert 'fcf' not in r['metrics'] and 'affo_growth' in r['metrics']
    assert r['status'] == 'INSUFFICIENT_DATA'


def test_future_information_and_restatements_as_of():
    f = fact('revenue', 100, '2025-01-01', '2025-12-31')
    amended = replace(f, value=80, filed='2026-11-01', accession='amended')
    assert versions([f, amended], AS_OF) == [f]
    assert versions([f, amended], date(2026, 11, 2)) == [amended]
    assert versions([f], date(2025, 12, 31)) == []


def test_currency_scope_or_standard_mismatch_prevents_normal():
    for key, value in [('unit', 'EUR'), ('scope', 'separate'), ('standard', 'ifrs-full')]:
        fs = sample()
        fs[0] = replace(fs[0], **{key: value})
        assert assessed(fs)['status'] == 'INSUFFICIENT_DATA'


def test_noncontiguous_quarters_and_stale_reports_block_normal():
    fs = [f for f in sample() if not (f.metric == 'revenue' and f.start == '2025-04-01')]
    assert assessed(fs)['status'] == 'INSUFFICIENT_DATA'
    assert evaluate(sample(), date(2028, 1, 1), 'GENERAL', CONFIG)['status'] == 'INSUFFICIENT_DATA'


def test_explicit_classification_as_of_and_unknown_default():
    assert classify({}, {}, AS_OF)[0] == 'UNKNOWN'
    for sic, kind in [('6021', 'BANK'), ('6311', 'INSURER'), ('6798', 'REIT'), ('6211', 'OTHER_FINANCIAL'), ('3571', 'GENERAL')]:
        assert classify({}, {'sic': sic, 'observed_on': str(AS_OF)}, AS_OF)[0] == kind
    assert classify({'industry': 'GENERAL', 'source': 'issuer', 'reviewed_on': '2027-01-01'}, {}, AS_OF)[0] == 'UNKNOWN'


def test_thresholds_single_warning_and_excess_dividend_only_warning():
    r = assessed(edit(sample(), 'dividends', 100))
    assert r['metrics']['dividend_fcf']['state'] == 'WARNING'
    assert r['status'] == 'WARNING'
    fs = edit(sample(), 'revenue', 89, lambda f: f.start >= '2025-10-01')
    assert assessed(fs)['metrics']['revenue_growth']['state'] == 'WARNING'


def test_transition_detection():
    normal = assessed()
    warning = assessed(edit(sample(), 'dividends', 100))
    high = assessed(edit(sample(), 'ocf', -100))
    assert event(normal, None) == 'INITIAL'
    assert event(warning, normal) == 'UPGRADE'
    assert event(high, warning) == 'UPGRADE'
    assert event(high, high) is None
    missing = assessed([])
    assert event(missing, normal) is None
    assert event(high, missing) == 'NEW_SEVERE'


def test_reports_bom_history_outbox_dedup_and_no_risk_exclusion(tmp_path):
    config = deepcopy(CONFIG)
    config['securities']['us:SAMPLE'] = {'industry': 'GENERAL', 'source': 'issuer', 'reviewed_on': str(AS_OF)}
    adapter = Mock()
    adapter.fetch.return_value = sample(), {'source': 'official fixture'}, []
    inputs = [('SAMPLE', '가상기업', 'A', '')]
    dividends = [{'code': 'SAMPLE', 'candidate': True, 'drawdown': -35, 'metrics': {'yield_pct': 5}}]
    store = Store(tmp_path / 'state.sqlite3')
    records, failed = run_market('us', inputs, [], dividends, tmp_path, config, store, AS_OF, adapter)
    assert not failed and records[0]['buy_candidate']
    initial = notifications(store, ['us:SAMPLE'], AS_OF)
    assert initial and 'INITIAL' in initial[0][1]
    assert (tmp_path / 'friendly_us_fundamental.csv').read_bytes().startswith(b'\xef\xbb\xbf')
    sender = Mock()
    deliver(store, initial, sender)
    deliver(store, initial, sender)
    assert sender.call_count == len(initial)
    run_market('us', inputs, [], dividends, tmp_path, config, store, AS_OF, adapter)
    assert notifications(store, ['us:SAMPLE'], AS_OF) == []
    adapter.fetch.return_value = edit(sample(), 'ocf', -100), {}, []
    records, _ = run_market('us', inputs, [], dividends, tmp_path, config, store, AS_OF, adapter)
    assert records[0]['analysis']['status'] == 'HIGH_RISK'
    assert records[0]['buy_candidate']
    assert 'UPGRADE' in notifications(store, ['us:SAMPLE'], AS_OF)[0][1]
    config['exclude_high_risk'] = True
    records, _ = run_market('us', inputs, [], dividends, tmp_path, config, store, AS_OF, adapter)
    assert not records[0]['buy_candidate']
    assert store.db.execute('SELECT COUNT(*) FROM evaluations').fetchone()[0] == 4
    store.close()


def test_uncertain_delivery_not_automatically_retried(tmp_path):
    store = Store(tmp_path / 'state.sqlite3')
    sender = Mock(side_effect=OSError('ambiguous'))
    with pytest.raises(OSError):
        deliver(store, [('alert', 'text')], sender)
    deliver(store, [('alert', 'text')], sender)
    assert sender.call_count == 1
    assert store.delivered('alert')[0] == 'UNCERTAIN'
    store.close()


def test_sec_tag_fallback_versions_future_and_currency():
    entry = {'start': '2025-01-01', 'end': '2025-12-31', 'filed': '2026-02-01', 'form': '10-K', 'accn': '001-01', 'val': 100}
    payload = {'cik': 1, 'facts': {'us-gaap': {'Revenues': {'units': {'USD': [entry, dict(entry, filed='2027-01-01', val=50)]}}}}}
    facts = parse_facts(payload, AS_OF)
    assert len(facts) == 1 and facts[0].value == 100 and facts[0].unit == 'USD'


def test_dart_cumulative_null_and_filing_guard():
    row = {'corp_code': '00123456', 'reprt_code': '11012', 'bsns_year': '2026', 'sj_div': 'IS',
           'account_id': 'ifrs-full_Revenue', 'rcept_no': '20260814000001', 'thstrm_amount': '100',
           'thstrm_add_amount': '230', 'currency': 'KRW'}
    fs = parse_statement({'list': [row]}, 2026, '11012', 'consolidated', AS_OF, '00123456')
    assert fs[0].value == 230 and fs[0].start == '2026-01-01'
    assert parse_statement({'list': [row]}, 2026, '11012', 'consolidated', date(2026, 8, 1), '00123456') == []
    row['thstrm_add_amount'] = '-'
    assert parse_statement({'list': [row]}, 2026, '11012', 'consolidated', AS_OF, '00123456') == []
    assert amount('-') is None and amount('0') == 0 and amount('(1,234)') == -1234


def test_europe_unsupported_and_official_import(tmp_path):
    assert Europe().fetch('EXAMPLE', {}, AS_OF)[0] == []
    path = tmp_path / 'official.json'
    path.write_text(json.dumps({'code': 'EXAMPLE', 'market': 'eu', 'facts': [sample()[0].dump()]}))
    assert Europe().fetch('EXAMPLE', {'facts_file': str(path)}, AS_OF)[0][0].value == 400
    with pytest.raises(ValueError):
        Europe().fetch('OTHER', {'facts_file': str(path)}, AS_OF)


def test_http_cache_and_error_redaction(tmp_path):
    store = Store(tmp_path / 'http.sqlite3')
    client = Client(store, CONFIG['http'])
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = b'{"facts":{}}'
    with patch('fundamentals.http.urlopen', return_value=response) as request:
        assert client.json('https://example.test?key=secret') == {'facts': {}}
        client.json('https://example.test?key=secret')
        assert request.call_count == 1
    assert 'secret' not in str(store.db.execute('SELECT * FROM cache').fetchall())
    with patch('fundamentals.http.urlopen', side_effect=OSError('secret')), patch('fundamentals.http.time.sleep'):
        with pytest.raises(ValueError, match='provider unavailable') as exc:
            client.json('https://other.test?key=secret')
        assert 'secret' not in str(exc.value)
    store.close()


def test_s3_requires_versioning_and_explicit_initialization(tmp_path):
    with patch('fundamental_state_sync.aws', return_value='{}'):
        with pytest.raises(ValueError, match='versioning'):
            sync('restore', tmp_path / 'state.sqlite3', 'bucket', 'key')
    with patch('fundamental_state_sync.aws', side_effect=['{"Status":"Enabled"}', '{}']):
        with pytest.raises(ValueError, match='initialization'):
            sync('restore', tmp_path / 'state.sqlite3', 'bucket', 'key')
    with patch('fundamental_state_sync.aws', side_effect=['{"Status":"Enabled"}', '{}']):
        sync('restore', tmp_path / 'state.sqlite3', 'bucket', 'key', initialize=True)


def test_invalid_config_and_fact(tmp_path):
    config = deepcopy(CONFIG)
    config['single_warning_status'] = 'NORMAL'
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError):
        load_config(path)
    with pytest.raises(ValueError):
        replace(sample()[0], value=float('nan'))


@pytest.mark.parametrize('value,state', [(90, 'WARNING'), (80, 'SEVERE'), (91, 'NORMAL')])
def test_exact_decline_thresholds(value, state):
    fs = edit(sample(), 'revenue', value, lambda f: f.start >= '2025-10-01')
    assert assessed(fs)['metrics']['revenue_growth']['state'] == state


@pytest.mark.parametrize('multiple,state', [(3.5, 'NORMAL'), (3.51, 'WARNING'), (5, 'WARNING'), (5.01, 'SEVERE')])
def test_debt_strict_thresholds(multiple, state):
    fs = edit(sample(), 'total_debt', multiple * 200 + 100)
    assert assessed(fs)['metrics']['net_debt_ebitda']['state'] == state


def test_bank_capital_reit_and_insurer_separate_thresholds():
    fs = sample() + [fact('cet1', 8, None, '2026-09-30', unit='percent'),
                     fact('npl', 6, None, '2026-09-30', unit='percent')]
    r = assessed(fs, 'BANK')
    assert r['metrics']['cet1']['state'] == 'WARNING'
    assert r['metrics']['npl']['state'] == 'SEVERE'
    assert r['status'] == 'WARNING'
    fs.append(fact('solvency', 150, None, '2026-09-30', unit='percent'))
    r = assessed(fs, 'INSURER')
    assert r['metrics']['solvency']['value'] == 150 and r['metrics']['solvency']['state'] == 'N/A'
    assert r['status'] == 'INSUFFICIENT_DATA'
    config = deepcopy(CONFIG)
    config['industries']['INSURER']['thresholds']['solvency'] = [180, 100]
    assert assessed(fs, 'INSURER', config)['metrics']['solvency']['state'] == 'WARNING'
    extras = [replace(f, metric='affo', tag='affo') for f in fs if f.metric == 'net_income']
    extras += [replace(f, metric='rental_income', tag='rental_income') for f in fs if f.metric == 'revenue']
    assert assessed(fs + extras, 'REIT')['status'] == 'NORMAL'


def test_annual_gap_and_latest_roe_missing_do_not_look_normal():
    fs = [f for f in sample() if not (f.metric == 'revenue' and f.days > 330 and f.end == '2021-12-31')]
    assert assessed(fs)['status'] == 'INSUFFICIENT_DATA'
    fs = [f for f in sample() if not (f.metric == 'equity' and f.end == '2025-12-31')]
    assert assessed(fs, 'BANK')['metrics']['roe']['value'] is None


def test_ksic_conservative_classification():
    for code, kind in [('12000', 'GENERAL'), ('64121', 'BANK'), ('65110', 'INSURER'),
                       ('64992', 'OTHER_FINANCIAL'), ('68200', 'UNKNOWN'), ('bad', 'UNKNOWN')]:
        assert classify({}, {'ksic': code, 'observed_on': str(AS_OF)}, AS_OF)[0] == kind


def test_sec_disclosure_change_refreshes_facts_without_daily_full_download(tmp_path):
    store = Store(tmp_path / 'state.sqlite3')
    client = Mock()
    client.store = store
    sub = {'cik': 1, 'tickers': ['SAMPLE'], 'sic': '3571', 'filings': {'recent': {'accessionNumber': ['one']}}}
    payload = {'cik': 1, 'facts': {}}
    def response(url, *args, **kwargs):
        return deepcopy(sub) if '/submissions/' in url else payload
    client.json.side_effect = response
    adapter = SEC(client, CONFIG)
    with patch.dict('os.environ', {'SEC_USER_AGENT': 'Fixture fixture@example.invalid'}):
        for _ in range(2):
            adapter.fetch('SAMPLE', {'cik': '1'}, AS_OF)
        calls = [c for c in client.json.call_args_list if '/companyfacts/' in c.args[0]]
        assert len(calls) == 1
        sub['filings']['recent']['accessionNumber'] = ['two']
        adapter.fetch('SAMPLE', {'cik': '1'}, AS_OF)
        calls = [c for c in client.json.call_args_list if '/companyfacts/' in c.args[0]]
        assert len(calls) == 2
    store.close()


def test_dart_namespace_refresh_and_consolidated_preference(tmp_path):
    store = Store(tmp_path / 'state.sqlite3')
    client = Mock()
    client.store = store
    adapter = DART(client, CONFIG)
    calls = []
    def call(endpoint, key, ttl=86400, **params):
        calls.append((endpoint, params))
        if endpoint == 'company':
            return {'stock_code': '000001', 'acc_mt': '12', 'induty_code': '12000'}
        if endpoint == 'list':
            return {'status': '000', 'list': [{'rcept_no': '20260320000001'}]}
        report, year = params['reprt_code'], params['bsns_year']
        return {'list': [{'corp_code': '00000001', 'reprt_code': report, 'bsns_year': str(year), 'sj_div': 'IS',
                          'account_id': 'ifrs-full_Revenue', 'rcept_no': f'{year+1}0320000001',
                          'thstrm_amount': '400', 'thstrm_add_amount': '200', 'currency': 'KRW'}]}
    with patch.dict('os.environ', {'OPENDART_API_KEY': 'fixture'}), patch.object(adapter, 'call', side_effect=call):
        facts, metadata, _ = adapter.fetch('000001', {'corp_code': '00000001'}, AS_OF)
        assert facts and {f.scope for f in facts} == {'consolidated'}
        assert metadata['ksic'] == '12000'
        assert all(p['fs_div'] == 'CFS' and p['revision'] for e, p in calls if e == 'fnlttSinglAcntAll')
        count = len(calls)
        adapter.fetch('000001', {'corp_code': '00000001'}, AS_OF)
        assert len(calls) == count + 2  # profile + inexpensive disclosure check only
    store.close()


def test_long_unicode_reports_and_redacted_isolated_adapter_failure(tmp_path):
    config = deepcopy(CONFIG)
    store = Store(tmp_path / 'state.sqlite3')
    adapter = Mock()
    adapter.fetch.side_effect = OSError('secret API key must not be exposed')
    rs, failed = run_market('us', [('SAMPLE', '가상😀' * 2000, 'A', '')], [],
                            [{'code': 'SAMPLE', 'candidate': True}], tmp_path, config, store, AS_OF, adapter)
    assert failed and rs[0]['analysis']['status'] == 'INSUFFICIENT_DATA'
    parts = message(rs[0], 'INITIAL')
    assert len(parts) > 1 and all(len(p.encode('utf-16-le')) // 2 <= 3400 for p in parts)
    assert 'secret API' not in (tmp_path / 'friendly_us_fundamental.audit.json').read_text(encoding='utf-8')
    store.close()


def test_daily_dry_run_integrates_financials_without_delivery(tmp_path):
    import daily_runner
    import stock_52week
    config = deepcopy(CONFIG)
    config['securities']['us:SAMPLE'] = {'industry': 'GENERAL', 'source': 'fixture', 'reviewed_on': str(AS_OF)}
    provider = Mock()
    provider.fetch.return_value = sample(), {}, []
    def quote(market, inputs, output):
        stock_52week.write_csv(output, stock_52week.HEADERS, [['SAMPLE', '가상기업', 'A', '', '-35']])
        output.with_suffix('.audit.json').write_text(json.dumps([{'code': 'SAMPLE', 'current': 65, 'high_52week': 100}]))
        return 0
    dividend = {'code': 'SAMPLE', 'candidate': True, 'metrics': {'yield_pct': 5}}
    with patch('sys.argv', ['daily_runner.py', '--market', 'us', '--dry-run', '--env-file', str(tmp_path / 'none'),
                            '--output-dir', str(tmp_path), '--state-dir', str(tmp_path / 'state')]), \
         patch.object(daily_runner.stocks, 'read_stocks', return_value=[('SAMPLE', '가상기업', 'A', '')]), \
         patch.object(daily_runner.stocks, 'run_market', side_effect=quote), \
         patch.object(daily_runner.dividends, 'run_market', return_value=([dividend], False)), \
         patch.object(daily_runner.dividends, 'build_messages', return_value=['기존 배당 알림']), \
         patch.object(daily_runner, 'load_fundamental_config', return_value=config), \
         patch('fundamentals.report.SEC', return_value=provider), \
         patch.object(daily_runner, 'send_message') as sender:
        assert daily_runner.main() == 0
    sender.assert_not_called()
    assert dividend['fundamental']['analysis']['status'] == 'NORMAL'
    assert not (tmp_path / 'state/fundamentals.sqlite3').exists()
    text = (tmp_path / 'telegram_preview.txt').read_text(encoding='utf-8')
    assert '기존 배당 알림' in text and '[펀더멘털 위험 분석 리포트]' in text


def test_four_quarter_ebitda_is_sufficient_without_prior_ebitda():
    fs = [f for f in sample() if f.metric not in {'ebitda', 'interest'} or f.start >= '2025-10-01']
    r = assessed(fs)
    assert r['metrics']['net_debt_ebitda']['value'] == .5
    assert r['metrics']['interest_coverage']['value'] == 5


def test_dart_restated_comparative_uses_actual_later_filing_date():
    row = {'corp_code': '00123456', 'reprt_code': '11011', 'bsns_year': '2025', 'sj_div': 'IS',
           'account_id': 'ifrs-full_Revenue', 'rcept_no': '20260320000001', 'thstrm_amount': '400',
           'frmtrm_amount': '350', 'bfefrmtrm_amount': '300', 'currency': 'KRW'}
    fs = parse_statement({'list': [row]}, 2025, '11011', 'consolidated', AS_OF, '00123456')
    prior = next(f for f in fs if f.end == '2024-12-31')
    assert prior.value == 350 and prior.filed == '2026-03-20'
    original = fact('revenue', 375, '2024-01-01', '2024-12-31', unit='KRW', standard='ifrs-full')
    assert next(f for f in versions([original] + fs, AS_OF) if f.end == '2024-12-31').value == 350
    assert versions([prior], date(2026, 3, 1)) == []
    # A future duplicate row must not supply comparative values to an earlier receipt.
    future = dict(row, rcept_no='20270320000001', frmtrm_amount='1')
    fs = parse_statement({'list': [future, row]}, 2025, '11011', 'consolidated', AS_OF, '00123456')
    assert next(f for f in fs if f.end == '2024-12-31').value == 350


def test_existing_dividend_cut_is_reused_with_source_and_date():
    context = {'as_of': str(AS_OF), 'source': 'verified dividend adapter',
               'metrics': {'cut': True, 'annual_dividends': {2024: 2, 2025: 1},
                           'annual_dividend_basis': 'DART fiscal-year DPS'}}
    r = evaluate(sample(), AS_OF, 'GENERAL', CONFIG, dividend_context=context)
    assert r['metrics']['dividend_cut']['state'] == 'WARNING'
    assert r['status'] == 'WARNING'
    context['as_of'] = '2027-01-01'
    r = evaluate(sample(), AS_OF, 'GENERAL', CONFIG, dividend_context=context)
    assert r['metrics']['dividend_cut']['value'] is None


def test_dividend_and_profit_growth_compare_same_annual_periods():
    fs = edit(sample(), 'dps', 1.1, lambda f: f.end == '2025-12-31')
    # Annual earnings rose 20%; current TTM earnings fell 50% in a different window.
    fs = edit(fs, 'net_income', 96, lambda f: f.days > 330 and f.end == '2025-12-31')
    fs = edit(fs, 'net_income', 10, lambda f: f.days < 110 and f.start >= '2025-10-01')
    review = {'dividend_split_verified': True, 'eps_source': 'verified split history',
              'source': 'issuer', 'reviewed_on': str(AS_OF)}
    r = evaluate(fs, AS_OF, 'GENERAL', CONFIG, review)
    assert r['metrics']['dividend_growth']['value'] == pytest.approx(10)
    assert r['metrics']['dividend_profit_growth']['value'] == pytest.approx(20)
    assert r['metrics']['dividend_growth']['state'] == 'NORMAL'


def test_s3_round_trip_and_corrupt_restore_preserves_local_state(tmp_path):
    import sqlite3
    import shutil
    path = tmp_path / 'local/state.sqlite3'
    remote = tmp_path / 'remote.sqlite3'
    store = Store(path)
    store.put('public', {'value': 1})
    store.close()
    def fake_aws(*args):
        if args[1] == 'get-bucket-versioning':
            return '{"Status":"Enabled"}'
        if args[1] == 'list-objects-v2':
            return '{"Contents":[{"Key":"key"}]}'
        if args[1] == 'put-object':
            shutil.copyfile(args[args.index('--body') + 1], remote)
        if args[1] == 'get-object':
            shutil.copyfile(remote, args[-1])
        return '{}'
    with patch('fundamental_state_sync.aws', side_effect=fake_aws):
        sync('save', path, 'bucket', 'key')
        restored = tmp_path / 'restored/state.sqlite3'
        sync('restore', restored, 'bucket', 'key')
        store = Store(restored)
        assert store.get('public', 3600) == {'value': 1}
        store.close()
        before = restored.read_bytes()
        remote.write_bytes(b'corrupt')
        with pytest.raises(sqlite3.DatabaseError):
            sync('restore', restored, 'bucket', 'key')
        assert restored.read_bytes() == before
