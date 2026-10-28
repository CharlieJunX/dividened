"""Deterministic samples: no live API or Telegram calls."""
import json
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import daily_runner
import dividend_data as data
import dividend_metrics as metrics
import dividend_report as report

AS_OF = date(2026, 10, 25)
CONFIG = report.load_config(Path(__file__).with_name('dividend_config.json'))


def sample(frequency=4):
    start = date(2019, 1, 1)
    prices = []
    while start <= AS_OF:
        prices.append({'date': start.isoformat(), 'close': 100})
        start += timedelta(days=1)
    months = {1: [6], 2: [6, 12], 4: [3, 6, 9, 12]}[frequency]
    dividends = [{'date': date(y, m, 15).isoformat(), 'amount': 4 / frequency,
                  'kind': 'regular', 'currency': 'USD'}
                 for y in range(2019, 2027) for m in months if date(y, m, 15) <= AS_OF]
    return {'symbol': 'SAMPLE', 'as_of': AS_OF.isoformat(), 'prices': prices,
            'dividends': dividends, 'splits': [], 'basis': 'split_adjusted', 'currency': 'USD',
            'source': 'fixed sample', 'retrieved_at': '2026-10-25T00:00:00Z'}


def calc(history, current=80, currency='USD', review=None):
    quality = {**CONFIG['quality'], 'quote_tolerance_pct': 30}
    return metrics.calculate(history, current, currency, review or {}, quality)


@pytest.mark.parametrize('frequency', [1, 2, 4])
def test_yields_and_full_month_averages(frequency):
    result = calc(sample(frequency))
    assert result['yield_pct'] == pytest.approx(5)
    assert result['avg3_pct'] == pytest.approx(4)
    assert result['avg5_pct'] == pytest.approx(4)
    assert result['ratio'] == pytest.approx(1.25)
    assert len(result['monthly3']) == 36
    assert result['monthly3'][-1]['date'] == '2026-09-30'
    assert result['cagr_pct'] == pytest.approx(0)
    assert result['status'] == '정상'


def test_boundary_and_cut_does_not_exclude():
    result = calc(sample())
    result.update(yield_pct=3, ratio=1.2, cut=True)
    assert metrics.is_candidate(result, -30, CONFIG['thresholds'])
    assert not metrics.is_candidate(result, -29.99, CONFIG['thresholds'])
    result['ratio'] = 1.19999
    assert not metrics.is_candidate(result, -30, CONFIG['thresholds'])
    result.update(ratio=1.2, status='확인 필요')
    assert not metrics.is_candidate(result, -30, CONFIG['thresholds'])


def test_special_dividend_excluded_and_unknown_blocks():
    history = sample()
    history['dividends'].append({'date': '2026-05-01', 'amount': 30, 'kind': 'special', 'currency': 'USD'})
    assert calc(history)['ttm'] == 4
    history['dividends'][-1]['kind'] = 'unknown'
    result = calc(history)
    assert result['status'] == '확인 필요'
    assert result['yield_pct'] is None and result['avg3_pct'] is None


def test_review_is_explicit_scoped_and_auditable():
    history = sample()
    for event in history['dividends']:
        event.update(kind='unknown', currency=None)
    review = {'source': 'issuer dividend register reviewed', 'from': '2019-01-01',
              'through': '2026-10-25', 'default_kind': 'regular', 'dividend_currency': 'USD'}
    assert calc(history, review=review)['status'] == '정상'
    review['through'] = '2025-12-31'
    assert calc(history, review=review)['status'] == '확인 필요'
    review.pop('source')
    assert calc(history, review=review)['ttm'] is None


def test_combined_regular_and_special_payment():
    history = sample()
    history['dividends'][-1].update(amount=11, kind='unknown')
    day = history['dividends'][-1]['date']
    review = {'source': 'issuer', 'from': day, 'through': day, 'dividend_currency': 'USD',
              'events': {day: {'kind': 'mixed', 'regular_amount': 1, 'special_amount': 10}}}
    assert calc(history, review=review)['ttm'] == 4
    review['events'][day]['special_amount'] = 9
    with pytest.raises(ValueError):
        calc(history, review=review)


def test_growth_five_years_and_cut():
    history = sample(1)
    for event in history['dividends']:
        year = int(event['date'][:4])
        event['amount'] = 4 * 1.1 ** (year - 2020)
    result = calc(history)
    assert result['cagr_pct'] == pytest.approx(10)
    assert result['cut'] is False
    history['dividends'][-1]['amount'] = 1
    assert calc(history)['cut'] is True


@pytest.mark.parametrize('ratio', [2, .2])
def test_raw_split_and_reverse_split_normalised_once(ratio):
    history = sample()
    baseline = calc(history)
    history['basis'] = 'raw'
    history['splits'] = [{'date': '2023-01-01', 'ratio': ratio}]
    for event in history['dividends']:
        if event['date'] < '2023-01-01':
            event['amount'] *= ratio
    for item in history['prices']:
        if item['date'] < '2023-01-01':
            item['close'] *= ratio
    result = calc(history)
    assert result['avg5_pct'] == baseline['avg5_pct']
    assert result['cagr_pct'] == baseline['cagr_pct']
    assert result['cut'] is False
    already_adjusted = sample()
    already_adjusted['splits'] = history['splits']
    assert calc(already_adjusted)['avg5_pct'] == baseline['avg5_pct']


def test_insufficient_history_not_averaged_over_fewer_months():
    history = sample()
    history['prices'] = [p for p in history['prices'] if p['date'] >= '2023-01-01']
    result = calc(history)
    assert result['avg3_pct'] is None
    assert result['avg5_pct'] is None
    assert result['cagr_pct'] is None
    assert result['status'] == '확인 필요'


def test_five_year_metric_optional_with_four_year_history():
    history = sample()
    history['prices'] = [p for p in history['prices'] if p['date'] >= '2022-01-01']
    result = calc(history)
    assert result['avg3_pct'] == 4
    assert result['avg5_pct'] is None and result['cagr_pct'] is None
    assert result['status'] == '정상'


@pytest.mark.parametrize('fault', ['zero', 'negative', 'duplicate', 'gap', 'currency', 'stale', 'infinite'])
def test_bad_data_never_candidate(fault):
    history = sample()
    if fault in {'zero', 'negative'}:
        history['prices'][100]['close'] = 0 if fault == 'zero' else -1
    elif fault == 'duplicate':
        history['dividends'].append(deepcopy(history['dividends'][-1]))
    elif fault == 'gap':
        history['dividends'].pop(-2)
    elif fault == 'currency':
        history['dividends'][-1]['currency'] = 'EUR'
    elif fault == 'stale':
        history['prices'] = history['prices'][:-30]
    else:
        history['prices'][100]['close'] = float('inf')
    result = calc(history)
    assert result['status'] == '확인 필요'
    assert not metrics.is_candidate(result, -40, CONFIG['thresholds'])


def test_isolated_missing_close_warns_without_blocking_candidate():
    history = sample()
    baseline = calc(history)
    for item in history['prices']:
        if item['date'] == '2025-09-19':
            item['close'] = None
    result = calc(history)
    assert result['status'] == '정상'
    assert result['avg3_pct'] == baseline['avg3_pct']
    assert result['avg5_pct'] == baseline['avg5_pct']
    assert result['missing_price_dates'] == ['2025-09-19']
    assert any('종가 없는 날짜' in w for w in result['warnings'])
    assert metrics.is_candidate(result, -40, CONFIG['thresholds'])


@pytest.mark.parametrize('null_rows', [False, True])
def test_weekend_month_end_uses_friday_without_future_prices(null_rows):
    history = sample()
    # 2026-05-31 is Sunday. June's deliberately different price must not leak
    # into the May calculation, regardless of absent rows or explicit nulls.
    prices = []
    for item in history['prices']:
        day = date.fromisoformat(item['date'])
        if day.weekday() >= 5:
            if null_rows:
                prices.append({**item, 'close': None})
        else:
            prices.append({**item, 'close': 200 if day >= date(2026, 6, 1) else 100})
    history['prices'] = prices
    result = calc(history, current=200)
    assert result['status'] == '정상'
    may = next(m for m in result['monthly3'] if m['date'] == '2026-05-31')
    assert may['price_date'] == '2026-05-29'
    assert may['yield_pct'] == 4


def test_long_missing_month_end_still_blocks_and_returns_na():
    history = sample()
    for item in history['prices']:
        if '2026-09-20' <= item['date'] <= '2026-09-30':
            item['close'] = None
    result = calc(history)
    assert '주가 이력 공백' in result['issues']
    assert result['avg3_pct'] is None
    assert not metrics.is_candidate(result, -40, CONFIG['thresholds'])


def test_missing_latest_prices_still_obey_staleness_limit():
    history = sample()
    for item in history['prices']:
        if item['date'] >= '2026-10-01':
            item['close'] = None
    result = calc(history)
    assert '최근 주가 지연' in result['issues']
    assert not metrics.is_candidate(result, -40, CONFIG['thresholds'])


def test_no_ttm_and_late_annual_payment_warn():
    history = sample(1)
    history['dividends'].pop()
    result = calc(history)
    assert '최근 12개월 정기배당 없음' in result['warnings']
    assert '배당 지급 지연/중단 가능성' in result['warnings']


def test_pence_price_and_pounds_dividends():
    history = sample()
    history['currency'] = 'GBp'
    for item in history['prices']:
        item['close'] = 10000
    for event in history['dividends']:
        event['currency'] = 'GBP'
    assert calc(history, 8000, 'GBp')['yield_pct'] == 5


def test_calendar_window_includes_exact_ex_date_excludes_start():
    dividends = [(date(2025, 10, 25), 100, 'regular'), (date(2026, 10, 25), 4, 'regular')]
    assert metrics.ttm(dividends, AS_OF) == 4
    assert data.shift_months(date(2024, 2, 29), -12) == date(2023, 2, 28)


def test_month_end_not_last_trading_day_for_dividend_window():
    history = sample()
    # September 30 is absent; the ex-date on September 29 must still count.
    history['prices'] = [p for p in history['prices'] if p['date'] < '2026-09-29']
    history['dividends'].append({'date': '2026-09-29', 'amount': 1, 'kind': 'regular', 'currency': 'USD'})
    prices, dividends, _ = metrics.normalise(history, {})
    _, series = metrics.month_end_yields(prices, dividends, AS_OF, 36, CONFIG['quality'])
    assert series[-1]['yield_pct'] == 5


def test_chart_adapter_uses_close_local_dates_and_retains_unknown():
    ts = 1792771200
    payload = {'chart': {'error': None, 'result': [{'meta': {'symbol': 'TEST', 'currency': 'USD',
                'exchangeTimezoneName': 'America/New_York'}, 'timestamp': [ts],
                'indicators': {'quote': [{'close': [100]}], 'adjclose': [{'adjclose': [1]}]},
                'events': {'dividends': {'a': {'date': ts, 'amount': 4}},
                           'splits': {'b': {'date': ts, 'numerator': 2, 'denominator': 1}}}}]}}
    parsed = data.parse_chart(payload, 'TEST', AS_OF)
    assert parsed['prices'][0]['close'] == 100
    assert parsed['prices'][0]['date'] == '2026-10-23'
    assert parsed['dividends'][0]['kind'] == 'unknown'
    assert parsed['splits'][0]['ratio'] == 2
    with pytest.raises(ValueError):
        data.parse_chart(payload, 'OTHER', AS_OF)


def test_fetch_retries_and_symbol_mapping():
    with patch.object(data, 'urlopen', side_effect=OSError('API down')) as call, patch.object(data.time, 'sleep'):
        with pytest.raises(OSError):
            data.fetch_yahoo_history('005930', 'kr', CONFIG, AS_OF)
    assert call.call_count == 3
    assert '005930.KS' in call.call_args.args[0].full_url


def test_api_failure_isolated_and_utf8_bom(tmp_path):
    audit = [{'code': c, 'name': c, 'current': 80, 'high_52week': 120, 'currency': 'USD'} for c in ['BAD', 'OK']]
    config = deepcopy(CONFIG)
    config['quality']['quote_tolerance_pct'] = 30
    with patch.object(report, 'fetch_history', side_effect=[OSError('API down'), sample()]):
        results, failed = report.run_market('us', [(c, c, 'A', '') for c in ['BAD', 'OK']], audit, tmp_path, config, AS_OF)
    assert failed and results[0]['metrics']['status'] == '조회 실패'
    assert results[1]['candidate']
    assert (tmp_path / 'friendly_us_dividend.csv').read_bytes().startswith(b'\xef\xbb\xbf')
    assert 'API down' in (tmp_path / 'friendly_us_dividend_errors.csv').read_text(encoding='utf-8-sig')


def test_messages_sort_limit_empty_and_sample_preview(tmp_path):
    m = calc(sample())
    record = {'market': 'us', 'code': 'SAMPLE', 'name': '가상기업', 'grade': 'A', 'drawdown': -34.5,
              'metrics': m, 'candidate': True, 'as_of': AS_OF.isoformat()}
    m.update(yield_pct=4.8, avg3_pct=3.2, avg5_pct=3, ratio=1.5, cagr_pct=8.5)
    other = deepcopy(record)
    other.update(code='HIGH')
    other['metrics']['ratio'] = 2
    config = deepcopy(CONFIG)
    config['notifications']['max_candidates'] = 1
    assert 'HIGH' in report.build_messages([record, other], config)[0]
    message = report.build_messages([record], config)[0]
    assert '1.50배' in message and '4.8%' in message and '최근 배당 삭감: 없음' in message
    assert report.build_messages([], config) == []
    config['notifications']['notify_empty'] = True
    assert '후보 없음' in report.build_messages([], config)[0]


def test_daily_dry_run_never_sends_and_keeps_legacy_alert(tmp_path):
    config = deepcopy(CONFIG)
    config['notifications']['notify_empty'] = True
    def fake_market(market, inputs, output):
        import stock_52week as stocks
        stocks.write_csv(output, stocks.HEADERS, [['SAMPLE', '가상기업', 'A', '', '-30.00']])
        output.with_suffix('.audit.json').write_text(json.dumps([{'code': 'SAMPLE', 'name': '가상기업',
            'current': 80, 'high_52week': 120, 'currency': 'USD'}]), encoding='utf-8')
        return 0
    with patch('sys.argv', ['daily_runner.py', '--market', 'us', '--dry-run', '--no-fundamentals',
                           '--fundamental-config', str(tmp_path / 'missing.json'), '--output-dir', str(tmp_path)]), \
         patch.object(daily_runner.stocks, 'read_stocks', return_value=[('SAMPLE', '가상기업', 'A', '')]), \
         patch.object(daily_runner.stocks, 'run_market', side_effect=fake_market), \
         patch.object(daily_runner.dividends, 'load_config', return_value=config), \
         patch.object(report, 'fetch_history', return_value=sample()), \
         patch.object(daily_runner, 'send_message') as send, \
         patch.object(daily_runner, 'load_dotenv'):
        assert daily_runner.main() == 0
    send.assert_not_called()
    preview = (tmp_path / 'telegram_preview.txt').read_text(encoding='utf-8')
    assert '52주 최고가 대비 -30%' in preview
    assert '[배당 저평가 탐지 리포트]' in preview


def test_config_invalid_values(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text('{"quality":{"history_years":5}}', encoding='utf-8')
    with pytest.raises(ValueError):
        report.load_config(path)


def test_currency_only_config_does_not_verify_dividend_kind(tmp_path):
    path = tmp_path / 'currency.json'
    path.write_text(json.dumps({'dividend_reviews': {'kr:033780': {'dividend_currency': 'KRW'}}}), encoding='utf-8')
    config = report.load_config(path)
    history = sample()
    history['currency'] = 'KRW'
    for event in history['dividends']:
        event.update(kind='unknown', currency=None)
    result = calc(history, currency='KRW', review=config['dividend_reviews']['kr:033780'])
    assert '배당 통화 확인 필요/불일치' not in result['issues']
    assert '정기/특별배당 확인 필요' in result['issues']
    assert result['yield_pct'] is None
    assert not metrics.is_candidate(result, -40, config['thresholds'])
    config['dividend_reviews']['kr:033780']['default_kind'] = 'regular'
    path.write_text(json.dumps(config), encoding='utf-8')
    with pytest.raises(ValueError):
        report.load_config(path)


@pytest.mark.parametrize('kind', ['special', 'unknown'])
def test_all_dividends_included_in_ttm_and_monthly_averages(kind):
    history = sample()
    history['dividends'].append({'date': '2026-05-01', 'amount': 2, 'kind': kind, 'currency': 'USD'})
    result = metrics.calculate(history, 80, 'USD', {},
                               {**CONFIG['quality'], 'quote_tolerance_pct': 30}, include_all=True)
    assert result['ttm'] == 6
    assert result['yield_pct'] == pytest.approx(7.5)
    assert result['avg3_pct'] == pytest.approx(4 + 2 * 5 / 36)
    assert result['avg5_pct'] == pytest.approx(4 + 2 * 5 / 60)
    assert result['status'] == '정상'
    assert metrics.is_candidate(result, -40, CONFIG['thresholds'])
    assert '정기/특별배당 확인 필요' not in result['issues']
    assert any('정기·특별배당 모두 포함' in w for w in result['warnings'])


def test_all_dividends_used_for_growth_and_cut():
    history = sample()
    history['dividends'].append({'date': '2025-05-01', 'amount': 2, 'kind': 'special', 'currency': 'USD'})
    result = metrics.calculate(history, 100, 'USD', {}, CONFIG['quality'], include_all=True)
    assert result['annual_dividends'][2025] == 6
    assert result['cagr_pct'] == pytest.approx(((6 / 4) ** .2 - 1) * 100)
    assert result['cut'] is True


def test_all_mode_still_blocks_missing_currency_and_duplicate_events():
    history = sample()
    history['dividends'][-1]['currency'] = None
    result = metrics.calculate(history, 100, 'USD', {}, CONFIG['quality'], include_all=True)
    assert result['yield_pct'] is None
    assert result['status'] == '확인 필요'
    history['dividends'][-1]['currency'] = 'USD'
    history['dividends'].append(deepcopy(history['dividends'][-1]))
    result = metrics.calculate(history, 100, 'USD', {}, CONFIG['quality'], include_all=True)
    assert '중복 배당락일' in result['issues']
    assert not metrics.is_candidate(result, -40, CONFIG['thresholds'])


@pytest.mark.parametrize('code', ['033780', '005930', '123456'])
def test_market_currency_covers_existing_and_new_korean_codes(code):
    assert report.get_review(CONFIG, 'kr', code) == {'dividend_currency': 'KRW'}
    assert report.get_review(CONFIG, 'us', code) == {'dividend_currency': 'USD'}
    assert report.get_review(CONFIG, 'eu', code) == {'dividend_currency': 'EUR'}


def test_custom_security_review_retains_market_defaults(tmp_path):
    path = tmp_path / 'custom.json'
    review = {'source': 'issuer', 'from': '2020-01-01', 'through': '2026-10-25',
              'default_kind': 'regular'}
    path.write_text(json.dumps({'dividend_reviews': {'kr:033780': review,
                              'us:AAPL': {'dividend_currency': 'USD'}}}), encoding='utf-8')
    config = report.load_config(path)
    resolved = report.get_review(config, 'kr', '033780')
    assert resolved == {**review, 'dividend_currency': 'KRW'}
    assert report.get_review(config, 'kr', '123456')['dividend_currency'] == 'KRW'
    assert report.get_review(config, 'us', 'AAPL')['dividend_currency'] == 'USD'
    resolved['source'] = 'changed'
    assert config['dividend_reviews']['kr:033780']['source'] == 'issuer'


def test_korean_market_currency_used_in_report_calculation(tmp_path):
    history = sample()
    history['currency'] = 'KRW'
    for event in history['dividends']:
        event.update(currency=None, kind='unknown')
    audit = [{'code': '123456', 'name': 'sample', 'current': 100,
              'high_52week': 150, 'currency': 'KRW'}]
    with patch.object(report, 'fetch_history', return_value=history):
        results, failed = report.run_market('kr', [('123456', 'sample', 'A', '')],
                                            audit, tmp_path, CONFIG, AS_OF)
    assert not failed
    assert results[0]['metrics']['ttm'] == 4
    assert results[0]['metrics']['status'] == '정상'
    saved = json.loads((tmp_path / 'friendly_kr_dividend.audit.json').read_text(encoding='utf-8'))
    assert saved[0]['review']['dividend_currency'] == 'KRW'
