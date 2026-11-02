from copy import deepcopy
from datetime import date, timedelta
import json
import pytest
from dividend_data import shift_months
from backtest.config import load as config_load, experiments, validate as validate_config
from backtest.data import known, available_day, fx, stamp, validate, write_cache, load
from backtest.signals import drawdown, point, decision, evaluate
from backtest.events import event_trade, run as events_run
from backtest.metrics import mdd, event_summary, portfolio_metrics
from backtest.portfolio import run_one
from fundamentals.config import load_config as fin_config
from investment.config import load_config as inv_config


@pytest.fixture
def sample():
    days, day = [], date(2019, 1, 1)
    while day <= date(2022, 1, 31):
        if day.weekday() < 5:
            days.append(str(day))
        day += timedelta(days=1)
    s = {'id': 'TEST', 'name': '가상 테스트', 'market': 'kr', 'currency': 'KRW', 'timezone': 'Asia/Seoul',
         'open_time': '09:00', 'close_time': '15:30', 'source': 'SYNTHETIC', 'version': '1',
         'retrieved_at': '2022-02-01T00:00:00+00:00', 'price_basis': 'raw', 'calendar_verified': True,
         'sessions': days, 'bars': [{'date': d, 'open': 100., 'high': 101., 'close': 100.} for d in days],
         'splits': [], 'dividends': [],
         'actions_coverage': {'verified': True, 'source': 'SYNTHETIC', 'from': days[0], 'through': days[-1]},
         'dividends_coverage': {'verified': True, 'source': 'SYNTHETIC', 'from': days[0], 'through': days[-1]}}
    data = {'schema_version': 1, 'securities': [s], 'fx': {}, 'benchmarks': {}}
    c = config_load()
    c.update(start='2020-01-01', end='2021-12-31', segments=[], initial_capital=1000,
             strategies=[{'id': 'price', 'drawdown_pct': -30}], holding_months=[3])
    for key in c['costs']:
        c['costs'][key] = 0
    return data, s, c


def signal(day='2020-01-30', **kwargs):
    return {'date': day, 'strategy': 'price', 'security': 'TEST', 'initial': False, 'common_eligible': True,
            'point': {}, 'segment': 'all', 'segment_end': '2021-12-31', **kwargs}


def policies():
    return {'fundamental': fin_config(), 'investment': inv_config(),
            'dividend': json.loads(open('dividend_config.json', encoding='utf-8-sig').read())}


def record(**kwargs):
    return {'period_end': '2019-12-31', 'published_date': '2020-01-03',
            'retrieved_at': '2022-01-01T00:00:00+00:00', 'source': 'SYNTHETIC',
            'version': '1', 'original_version_verified': True, **kwargs}


def bar(s, day):
    return next(b for b in s['bars'] if b['date'] == day)


@pytest.mark.parametrize('months,exit_date', [(3, '2020-04-30'), (6, '2020-07-31'), (12, '2021-02-01')])
def test_next_open_calendar_month_end_and_weekend(sample, months, exit_date):
    data, s, c = sample
    bar(s, '2020-01-31')['open'] = 101
    r = event_trade(data, s, signal(), months, c)
    assert r['entry_date'] == '2020-01-31' and r['entry_price'] == 101
    assert r['exit_date'] == exit_date
    assert shift_months(date(2020, 1, 31), 1) == date(2020, 2, 29)


def test_exchange_holiday_and_missing_open_not_close_fallback(sample):
    data, s, c = sample
    s['sessions'].remove('2020-01-31')
    s['bars'] = [b for b in s['bars'] if b['date'] != '2020-01-31']
    r = event_trade(data, s, signal(), 3, c)
    assert r['entry_date'] == '2020-02-03' and r['exit_date'] == '2020-05-04'
    bar(s, '2020-02-03')['open'] = None
    r = event_trade(data, s, signal(), 3, c)
    assert r['entry_date'] is None and r['reason'] == 'ENTRY_MISSING_OPEN'


def test_delayed_exit_suspension_and_unresolved(sample):
    data, s, c = sample
    bar(s, '2020-04-30')['tradable'] = False
    bar(s, '2020-04-30')['status'] = 'SUSPENDED'
    r = event_trade(data, s, signal(), 3, c)
    assert r['exit_date'] == '2020-05-01'
    assert r['delayed_exit'][0]['reason'] == 'SUSPENDED'
    for b in s['bars']:
        if b['date'] >= '2020-04-30':
            b['open'] = None
    r = event_trade(data, s, signal(), 3, c)
    assert r['status'] == 'UNRESOLVED' and r['net_return_pct'] is None


def test_unverified_calendar_prevents_execution(sample):
    data, s, c = sample
    s['calendar_verified'] = False
    assert event_trade(data, s, signal(), 3, c)['reason'] == 'UNVERIFIED_EXCHANGE_CALENDAR'


def test_future_prices_and_disclosures_cannot_change_past_point(sample):
    data, s, c = sample
    p = point(s, '2020-01-30', c, policies())
    for b in s['bars']:
        if b['date'] > '2020-01-30':
            b['close'] *= 100
            b['high'] *= 100
    s['grades'] = [record(published_date='2021-01-01', grade='S')]
    assert point(s, '2020-01-30', c, policies()) == p


def test_publication_times_unknown_times_and_revisions(sample):
    _, s, _ = sample
    original = record(published_at='2020-01-03T15:00:00+09:00', value=10)
    revised = record(published_at='2020-01-03T16:00:00+09:00', value=20, version='2')
    assert known([original, revised], s, '2020-01-03') == [original]
    assert known([original, revised], s, '2020-01-06')[-1]['value'] == 20
    assert available_day(record(), s) == '2020-01-06'
    assert available_day(record(original_version_verified=False), s) is None
    assert known([record(published_date='2021-01-01')], s, '2020-01-03') == []


def test_high_window_is_split_adjusted_and_requires_all_observations(sample):
    _, s, c = sample
    c['lookback_sessions'] = 2
    s['splits'] = [{'date': '2020-01-31', 'ratio': 2}]
    bar(s, '2020-01-30')['high'] = 200
    bar(s, '2020-01-31').update(high=80, close=70)
    assert drawdown(s, '2020-01-31', c)[0] == pytest.approx(-30)
    bar(s, '2020-01-30')['high'] = None
    assert drawdown(s, '2020-01-31', c)[0] is None


def test_missing_condition_never_treated_as_pass(sample):
    _, s, c = sample
    p = point(s, '2020-01-30', c, policies())
    p['drawdown_pct'] = -50
    assert decision(p, {'drawdown_pct': -30, 'score_min': 70}, policies())[0] is None


def test_transition_missing_state_does_not_reset_and_initial_tag(sample, monkeypatch):
    import backtest.signals as module
    data, s, c = sample
    c.update(start='2020-01-01', end='2020-01-08')
    states = dict(zip([d for d in s['sessions'] if c['start'] <= d <= c['end']], [True, None, True, False, True, True]))
    monkeypatch.setattr(module, 'point', lambda s, d, c, p: {'date': d})
    monkeypatch.setattr(module, 'decision', lambda p, s, c: (states[p['date']], 'missing' if states[p['date']] is None else ''))
    rows, found = evaluate(data, c, {})
    assert [r['date'] for r in found] == ['2020-01-01', '2020-01-07']
    assert found[0]['initial'] is True and found[1]['initial'] is False


def test_overlap_counts_and_allow_overlap(sample):
    data, s, c = sample
    signals = [signal(), signal('2020-02-04')]
    rows = events_run(data, signals, c)
    summary = event_summary(rows, 20)
    assert summary['signals'] == 2 and summary['entries'] == 1 and summary['overlap_excluded'] == 1
    c['allow_overlap'] = True
    assert event_summary(events_run(data, signals, c), 20)['entries'] == 2


def test_split_dividend_and_costs_hand_calculation(sample):
    data, s, c = sample
    s['splits'] = [{'date': '2020-02-03', 'ratio': 2}]
    for b in s['bars']:
        if b['date'] >= '2020-02-03':
            b.update(open=55., high=55., close=55.)
    s['dividends'] = [{'id': '1', 'date': '2020-02-04', 'pay_date': '2020-02-10', 'amount': 1., 'currency': 'KRW'}]
    r = event_trade(data, s, signal(), 3, c)
    assert r['price_return_pct'] == pytest.approx(10)
    assert r['total_return_pct'] == pytest.approx(12)
    c['costs'].update(buy_fee=.01, sell_fee=.01, sell_tax=.02, dividend_withholding=.1)
    r = event_trade(data, s, signal(), 3, c)
    assert r['net_return_pct'] == pytest.approx(((110 * .97 + 2 * .9) / 101 - 1) * 100)


def test_ex_date_buyer_not_entitled_seller_is_entitled(sample):
    data, s, c = sample
    s['dividends'] = [{'id': '1', 'date': '2020-01-31', 'pay_date': '2020-02-10', 'amount': 10, 'currency': 'KRW'},
                      {'id': '2', 'date': '2020-04-30', 'pay_date': '2020-05-10', 'amount': 2, 'currency': 'KRW'}]
    r = event_trade(data, s, signal(), 3, c)
    assert r['total_return_pct'] == pytest.approx(2)
    assert len(r['dividends']) == 1


def test_adjusted_prices_rejected_and_missing_dividend_not_zero(sample):
    data, s, c = sample
    s['price_basis'] = 'dividend_adjusted'
    with pytest.raises(ValueError):
        validate(data)
    s['price_basis'] = 'raw'
    s['dividends_coverage']['verified'] = False
    r = event_trade(data, s, signal(), 3, c)
    assert r['price_return_pct'] == 0 and r['net_return_pct'] is None


def test_fx_direction_past_only_age_and_cash_flow_dates(sample):
    data, s, c = sample
    s['currency'] = 'USD'
    data['fx']['USD'] = [{'date': '2020-01-30', 'krw_per_unit': 1000},
                        {'date': '2020-04-29', 'krw_per_unit': 1200},
                        {'date': '2020-05-01', 'krw_per_unit': 9999}]
    r = event_trade(data, s, signal(), 3, c)
    assert r['net_return_pct'] == 0 and r['krw_net_return_pct'] == pytest.approx(20)
    assert fx(data, 'USD', '2020-01-29', 5) is None
    assert fx(data, 'USD', '2020-02-20', 5) is None
    assert fx(data, 'USD', '2020-04-29', 5, stamp('2020-04-29T09:00:00+09:00')) is None


def test_mdd_previous_peak_not_entry_low(sample):
    data, s, c = sample
    for b in s['bars']:
        if b['date'] >= '2020-01-31':
            b.update(open=120, close=120)
    bar(s, '2020-01-31').update(open=100, close=200)
    r = event_trade(data, s, signal(), 3, c)
    assert r['mdd_pct'] == pytest.approx(-40)
    assert mdd([100, 200, 120]) == pytest.approx(-40)


def test_benchmark_missing_not_zero_and_matched_price_basis(sample):
    data, s, c = sample
    assert event_trade(data, s, signal(), 3, c)['benchmark_return_pct'] is None
    c['benchmarks']['kr'] = 'TEST_INDEX'
    data['benchmarks']['TEST_INDEX'] = {'currency': 'KRW', 'basis': 'price', 'source': 'SYNTHETIC',
                                     'bars': [{'date': '2020-01-31', 'open': 100}, {'date': '2020-04-30', 'open': 110}]}
    r = event_trade(data, s, signal(), 3, c)
    assert r['benchmark_return_pct'] == pytest.approx(10) and r['excess_return_pct'] == pytest.approx(-10)


def test_incomplete_not_in_win_denominator_and_segment_embargo(sample):
    data, s, c = sample
    complete = event_trade(data, s, signal(), 3, c)
    incomplete = event_trade(data, s, signal(segment_end='2020-03-31'), 3, c)
    assert incomplete['status'] == 'INCOMPLETE' and incomplete['net_return_pct'] is None
    summary = event_summary([complete, incomplete], 20)
    assert summary['complete'] == 1 and summary['net_evaluable'] == 1 and summary['win_rate_pct'] == 0


def test_portfolio_cash_limit_fixed_id_priority_and_nav_identity(sample):
    data, s, c = sample
    other = deepcopy(s)
    other['id'] = 'ZZZ'
    data['securities'].append(other)
    c.update(target_weight=.6, max_positions=10)
    result = run_one(data, [signal(), signal(security='ZZZ')], c, 'price', 3,
                     {'name': 'all', 'start': c['start'], 'end': c['end']}, 'all')
    buys = [r for r in result['ledger'] if r['action'] == 'BUY']
    assert len(buys) == 1 and buys[0]['security'] == 'TEST' and buys[0]['shares'] == 6
    assert any(r.get('reason') == 'INSUFFICIENT_CASH' for r in result['ledger'])
    assert all(r['cash'] >= 0 for r in result['curve'])
    for row in result['curve']:
        assert row['nav'] == pytest.approx(row['cash'] + row['positions_value'] + row['receivables'])
    assert result['metrics']['total_return_pct'] == 0


def test_portfolio_entitlement_payment_and_no_reinvestment(sample):
    data, s, c = sample
    s['dividends'] = [record(id='1', date='2020-02-03', pay_date='2020-02-10', amount=10, currency='KRW')]
    result = run_one(data, [signal(), signal('2020-02-04')], c, 'price', 3,
                     {'name': 'all', 'start': c['start'], 'end': c['end']}, 'all')
    rows = {r['date']: r for r in result['curve']}
    assert rows['2020-02-03']['cash'] == 900 and rows['2020-02-03']['receivables'] == 10
    assert rows['2020-02-10']['cash'] == 910 and rows['2020-02-10']['investable_cash'] == 900
    assert rows['2020-02-10']['receivables'] == 0
    assert any(r.get('reason') == 'ALREADY_HELD' for r in result['ledger'])


def test_unknown_dividend_amount_cannot_inflate_purchase_nav(sample):
    data, s, c = sample
    s['dividends'] = [record(id='1', date='2020-02-03', pay_date='2020-02-10', amount=10,
                             currency='KRW', published_date='2021-01-01')]
    result = run_one(data, [signal()], c, 'price', 3,
                     {'name': 'all', 'start': c['start'], 'end': c['end']}, 'all')
    assert result['metrics']['total_return_pct'] is None
    assert any(r['action'] == 'UNRESOLVED_ENTITLEMENT' for r in result['ledger'])


def test_fx_cost_applies_only_to_currency_conversion(sample):
    data, s, c = sample
    c['costs']['fx_fee'] = .02
    r = event_trade(data, s, signal(), 3, c)
    assert r['krw_net_return_pct'] == 0
    result = run_one(data, [signal()], c, 'price', 3,
                     {'name': 'all', 'start': c['start'], 'end': c['end']}, 'all')
    assert result['metrics']['total_return_pct'] == 0


def test_missing_nav_suppresses_portfolio_statistics(sample):
    data, s, c = sample
    bar(s, '2020-02-03')['close'] = None
    result = run_one(data, [signal()], c, 'price', 3,
                     {'name': 'all', 'start': c['start'], 'end': c['end']}, 'all')
    assert result['metrics']['cagr_pct'] is None and result['metrics']['sharpe'] is None
    assert result['metrics']['mdd_pct'] is None


def test_same_open_split_precedes_other_security_order_sizing(sample):
    data, s, c = sample
    other = deepcopy(s)
    other['id'] = 'AAA'
    data['securities'].append(other)
    s['splits'] = [{'date': '2020-02-03', 'ratio': 2}]
    for b in s['bars']:
        if b['date'] >= '2020-02-03':
            b.update(open=50, close=50, high=50)
    result = run_one(data, [signal(), signal('2020-01-31', security='AAA')], c, 'price', 3,
                     {'name': 'all', 'start': c['start'], 'end': c['end']}, 'all')
    buy = next(r for r in result['ledger'] if r['action'] == 'BUY' and r['security'] == 'AAA')
    assert buy['nav_before'] == 1000 and buy['shares'] == 1


def test_cache_invalid_replacement_preserves_valid_file(sample, tmp_path):
    data, s, c = sample
    path = tmp_path / 'data.json'
    write_cache(path, data)
    s['bars'][0]['open'] = -1
    with pytest.raises(ValueError):
        write_cache(path, data)
    assert load(path)['securities'][0]['bars'][0]['open'] == 100


def test_sensitivity_records_all_fixed_conditions_no_optimization(sample):
    _, _, c = sample
    c['sensitivity'] = {'drawdown_pct': [-20, -30, -40], 'score_min': [70, 85]}
    assert len(list(experiments(c))) == 6
    c['segments'] = [{'name': 'train', 'start': '2020-01-01', 'end': '2020-12-31'},
                     {'name': 'test', 'start': '2020-12-31', 'end': '2021-12-31'}]
    with pytest.raises(ValueError):
        validate_config(c)
