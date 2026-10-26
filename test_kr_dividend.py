"""Official schema fixtures; no credentials or live requests required."""
import io
import json
import zipfile
from datetime import date
from unittest.mock import patch

import pytest

import dividend_data as data
import dividend_metrics as metrics
import dividend_report as report
import kr_dividend_data as kr
from test_dividend import sample, AS_OF, CONFIG

CRNO = '1101110000001'
CORP = '00123456'
KEYS = {'DATA_GO_KR_SERVICE_KEY': 'fsc-secret', 'OPENDART_API_KEY': 'dart-secret'}


def row(record='20251231', amount='1,350', snapshot='20260401', code='138040', **extra):
    return {'basDt': snapshot, 'crno': CRNO, 'isinCd': 'KR7' + code + '001',
            'dvdnBasDt': record, 'cashDvdnPayDt': '20260420',
            'stckGenrDvdnAmt': amount, 'stckGrdnDvdnAmt': '0', **extra}


def dart(amount='1,350', receipt='20260320000001', **extra):
    return {'status': '000', 'list': [{'corp_code': CORP, 'rcept_no': receipt,
             'se': '주당 현금배당금(원)', 'stock_knd': '보통주', 'thstrm': amount, **extra}]}


def fsc(items, total=None):
    return {'response': {'header': {'resultCode': '00'}, 'body': {
        'totalCount': len(items) if total is None else total, 'items': {'item': items}}}}


def korean_prices(*args):
    h = sample(1)
    h.update(currency='KRW', requested_start='2019-01-01', source_url='https://prices.example')
    # These Yahoo dividends must all be replaced.
    return h


def test_exact_security_latest_snapshot_and_raw_preservation():
    rows = [row(amount='100', snapshot='20260301'), row(), row(),
            row(code='138045', amount='9999'), row(snapshot='20270101', amount='9999')]
    events = kr.parse_fsc_rows(rows, '138040', CRNO, date(2019, 1, 1), AS_OF)
    assert len(events) == 1 and events[0]['amount'] == 1350
    assert events[0]['date'] == '2025-12-31'
    assert events[0]['payment_date'] == '2026-04-20'
    assert events[0]['date_basis'] == 'record_date'
    assert events[0]['raw'] == rows[1]


@pytest.mark.parametrize('change', [{'stckGenrDvdnAmt': ''}, {'stckGenrDvdnAmt': 'NaN'},
                                   {'crno': 'wrong'}, {'stckGrdnDvdnAmt': 'NaN'},
                                   {'dvdnBasDt': '20250230'}])
def test_bad_official_data_never_becomes_zero(change):
    with pytest.raises(ValueError):
        kr.parse_fsc_rows([row(**change)], '138040', CRNO, date(2019, 1, 1), AS_OF)


def test_conflicting_same_day_records_and_empty_results_fail():
    for rows in [[], [row(), row(amount='500')], [row(code='138045')]]:
        with pytest.raises(ValueError):
            kr.parse_fsc_rows(rows, '138040', CRNO, date(2019, 1, 1), AS_OF)


@pytest.mark.parametrize('differential', ['472', '900', '0', '', '-'])
def test_differential_dividend_uses_general_shareholder_amount(differential):
    original = row(code='024110', record='20191231', amount='670', stckGrdnDvdnAmt=differential)
    events = kr.parse_fsc_rows([original], '024110', CRNO, date(2019, 1, 1), AS_OF)
    assert events[0]['amount'] == 670 and events[0]['raw_amount'] == 670
    assert events[0]['shareholder_basis'] == 'general'
    assert events[0]['amount_field'] == 'stckGenrDvdnAmt'
    assert events[0]['raw_differential_amount'] == (float(differential) if differential not in ('', '-') else None)
    assert events[0]['raw'] == original


def test_differential_history_does_not_block_yield_metrics_or_candidate():
    original = row(record='20251231', amount='4', stckGrdnDvdnAmt='2')
    event = kr.parse_fsc_rows([original], '138040', CRNO, date(2019, 1, 1), AS_OF)[0]
    h = fiscal_sample()
    h['dividend_shareholder_basis'] = 'general'
    # Preserve dates while using the official general amount for every event.
    h['dividends'] = [{**event, 'date': old['date']} for old in h['dividends']]
    result = metrics.calculate(h, 80, 'KRW', {},
                               {**CONFIG['quality'], 'quote_tolerance_pct': 30}, include_all=True)
    assert result['ttm'] == 4 and result['avg3_pct'] == 4
    assert result['status'] == '정상'
    assert '일반주주 기준' in result['dividend_basis']
    assert metrics.is_candidate(result, -40, CONFIG['thresholds'])


def test_pagination_single_item_and_total_changes():
    with patch.object(kr, 'request_json', side_effect=[fsc([row()], 2), fsc(row())]) as call:
        # A changed total must not return a silently partial history.
        with pytest.raises(ValueError):
            kr.fetch_fsc_rows('key', CRNO)
    with patch.object(kr, 'request_json', side_effect=[fsc([row()], 2), fsc([row(record='20241231')], 2)]):
        assert len(kr.fetch_fsc_rows('key', CRNO)) == 2
    with patch.object(kr, 'request_json', return_value=fsc(row(), 1)):
        assert len(kr.fetch_fsc_rows('key', CRNO)) == 1


def test_encoded_key_and_no_credentials_in_error():
    with patch.object(kr, 'request_json', return_value=fsc([], 0)) as call:
        assert kr.fetch_fsc_rows('a%2Bb%3D', CRNO) == []
    assert call.call_args.args[1]['serviceKey'] == 'a+b='
    with patch.object(kr, 'urlopen', side_effect=OSError('https://host/?key=secret')), patch.object(kr.time, 'sleep'):
        with pytest.raises(ValueError) as error:
            kr.request_json(kr.FSC_URL, {'serviceKey': 'secret'})
    assert 'secret' not in str(error.value)
    with patch.object(kr, 'request_bytes', return_value=b'<error>secret</error>'):
        with pytest.raises(ValueError) as error:
            kr.request_json(kr.FSC_URL, {})
    assert 'secret' not in str(error.value)


def test_dart_stock_kind_and_future_filing_never_used():
    assert kr.parse_dart_annual(dart(), CORP, '보통주', AS_OF)['amount'] == 1350
    for payload in [dart('-'), dart(receipt='20270101000001'), dart(stock_knd='우선주'), {'status': '013'}]:
        assert kr.parse_dart_annual(payload, CORP, '보통주', AS_OF)['amount'] is None
    with pytest.raises(ValueError):
        kr.parse_dart_annual(dart(corp_code='99999999'), CORP, '보통주', AS_OF)


def test_dart_diagnostics_distinguish_unlabelled_dash_and_missing_report():
    unlabelled = kr.parse_dart_annual(dart(stock_knd='-'), CORP, '보통주', AS_OF)
    dash = kr.parse_dart_annual(dart('-'), CORP, '보통주', AS_OF)
    missing = kr.parse_dart_annual({'status': '013'}, CORP, '보통주', AS_OF)
    assert unlabelled['reason_code'] == 'stock_kind_missing'
    assert unlabelled['raw_rows'][0]['thstrm'] == '1,350'
    assert dash['reason_code'] == 'dividend_amount_dash'
    assert missing['reason_code'] == 'report_missing'
    annual = {2023: missing, 2024: unlabelled, 2025: dash}
    checks, issues = kr.cross_check([], annual, [])
    assert len(set(issues)) == 3
    assert all('DART 자료 미확인' not in issue for issue in issues)
    assert checks[1]['dart_reason_code'] == 'stock_kind_missing'
    assert '주식종류 미표기' in checks[1]['status']
    assert '-로 공시됨' in checks[2]['status']


def test_meritz_2024_resolved_by_2025_prior_year_column_without_overwriting_2025():
    old = dart('1,350', stock_knd='-', receipt='20250328001697')
    old['list'].extend(dart('-', stock_knd='-', receipt='20250328001697')['list'])
    recent = dart('-', frmtrm='1,350', lwfr='2,360', receipt='20260331004202')
    annual = kr.resolve_dart_annual({2024: old, 2025: recent}, CORP, '보통주', AS_OF)
    assert annual[2024]['amount'] == 1350
    assert annual[2024]['amount_field'] == 'frmtrm'
    assert annual[2024]['source_fiscal_year'] == 2025
    assert annual[2024]['target_fiscal_year'] == 2024
    assert annual[2024]['original_analysis']['raw_rows'] == old['list']
    assert annual[2025]['amount'] is None
    assert annual[2025]['reason_code'] == 'dividend_amount_dash'
    checks, _ = kr.cross_check([{'date': '2025-02-28', 'raw_amount': 1350}], annual, [])
    assert '귀속' in checks[0]['status']  # fiscal 2024 != record-date year 2025
    assert checks[0]['dart_source_url'].endswith('20260331004202')


@pytest.mark.parametrize('change', [{'stock_knd': '우선주'}, {'frmtrm': '-'},
                                   {'receipt': '20270101000001'}])
def test_prior_year_recovery_requires_known_same_share_kind_and_available_filing(change):
    recent = dart('-', frmtrm='1,350', **change) if 'frmtrm' not in change else dart('-', **change)
    annual = kr.resolve_dart_annual({2024: dart(stock_knd='-'), 2025: recent}, CORP, '보통주', AS_OF)
    assert annual[2024]['amount'] is None


def test_dart_explicit_zero_is_known_but_dash_is_not_overridden():
    annual = kr.resolve_dart_annual({2023: dart(0), 2024: dart('-'),
                                    2025: dart('100', frmtrm='50')}, CORP, '보통주', AS_OF)
    assert annual[2023]['amount'] == 0
    assert annual[2024]['amount'] is None
    assert annual[2024]['reason_code'] == 'dividend_amount_dash'


def test_fiscal_year_difference_is_not_silently_shifted_or_called_a_cut():
    events = [{'date': '2026-02-28', 'raw_amount': 1350}]
    checks, warnings = kr.cross_check(events, {2025: {'amount': 1350}}, [])
    assert checks[0]['record_year_amount'] == 0
    assert '귀속' in warnings[0]
    h = korean_prices()
    h.update(dividend_warnings=warnings, dividend_date_basis='record_date')
    for event in h['dividends']:
        event['currency'] = 'KRW'
    result = metrics.calculate(h, 100, 'KRW', {}, CONFIG['quality'], include_all=True)
    assert result['ttm'] == 4
    assert result['avg3_pct'] == 4 and result['cut'] is None
    assert result['status'] == '정상'
    assert warnings[0] in result['warnings']
    assert warnings[0] not in result['issues']


def test_corp_code_zip_maps_exact_stock_and_is_cached():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as archive:
        archive.writestr('CORPCODE.xml', '<result><list><stock_code>138040</stock_code>'
                          '<corp_code>00123456</corp_code></list></result>')
    kr.corporation_codes.cache_clear()
    with patch.object(kr, 'request_bytes', return_value=buf.getvalue()) as call:
        assert kr.corporation_codes('test')['138040'] == CORP
        assert '138045' not in kr.corporation_codes('test')
    assert call.call_count == 1
    kr.corporation_codes.cache_clear()


def test_domestic_route_replaces_yahoo_dividends_and_retains_audit():
    rows = [row(record=f'{year}1231', amount='100', snapshot='20260401') for year in range(2019, 2026)]
    def fake_dart(endpoint, key, **params):
        if endpoint == 'company':
            return {'status': '000', 'corp_code': CORP, 'stock_code': '138040',
                    'jurir_no': CRNO, 'acc_mt': '12'}
        return dart('100')
    with patch.dict('os.environ', KEYS), patch.object(kr, 'corporation_codes', return_value={'138040': CORP}), \
         patch.object(kr, 'dart_json', side_effect=fake_dart), \
         patch.object(kr, 'fetch_fsc_rows', return_value=rows), \
         patch.object(data, 'fetch_yahoo_history', side_effect=korean_prices):
        history = data.fetch_history('138040', 'kr', CONFIG, AS_OF)
    assert len(history['dividends']) == 7
    assert history['dividends'][-1]['amount'] == 100
    assert not history['dividend_issues']
    assert len(history['annual_cross_checks']) == 6
    assert history['dividend_date_basis'] == 'record_date'
    assert 'Yahoo' in history['price_source']['source'] or history['price_source']['source'] == 'fixed sample'
    assert 'secret' not in json.dumps(history)
    result = metrics.calculate(history, 10000, 'KRW', {},
                               {**CONFIG['quality'], 'quote_tolerance_pct': 100000}, include_all=True)
    assert result['ttm'] == 100
    assert '배당기준일 기준' in result['dividend_basis']


def test_split_adjustment_only_applied_to_official_dividends_once():
    def prices(*args):
        h = korean_prices()
        h['splits'] = [{'date': '2024-06-01', 'ratio': 2}]
        return h
    def fake_dart(endpoint, key, **params):
        return ({'status': '000', 'corp_code': CORP, 'stock_code': '138040',
                 'jurir_no': CRNO, 'acc_mt': '12'} if endpoint == 'company' else dart('100'))
    with patch.dict('os.environ', KEYS), patch.object(kr, 'corporation_codes', return_value={'138040': CORP}), \
         patch.object(kr, 'dart_json', side_effect=fake_dart), \
         patch.object(kr, 'fetch_fsc_rows', return_value=[row(record='20231231', amount='100')]):
        h = kr.fetch_korean_history('138040', CONFIG, AS_OF, prices)
    assert h['dividends'][0]['amount'] == 50 and h['dividends'][0]['raw_amount'] == 100
    assert h['prices'][0]['close'] == 100
    assert h['basis'] == 'split_adjusted'
    assert not h['dividend_issues'] and h['dividend_warnings']


def test_missing_credentials_fail_before_any_price_request():
    with patch.dict('os.environ', {}, clear=True), patch.object(data, 'fetch_yahoo_history') as fetch:
        with pytest.raises(ValueError, match='DATA_GO_KR_SERVICE_KEY'):
            data.fetch_history('138040', 'kr', CONFIG, AS_OF)
    fetch.assert_not_called()


def test_domestic_empty_ttm_is_na_not_zero():
    h = korean_prices()
    h['dividend_date_basis'] = 'record_date'
    h['dividends'] = []
    result = metrics.calculate(h, 100, 'KRW', {}, CONFIG['quality'], include_all=True)
    assert result['ttm'] is None and result['yield_pct'] is None
    assert result['cut'] is None and result['status'] == '확인 필요'
    assert '최근 5년 배당 삭감 감지' not in result['warnings']
    assert 'TTM 배당 없음' not in result['issues']


def test_missing_keys_saved_as_failure_with_na_and_next_security_continues(tmp_path):
    snapshots = [{'code': c, 'name': c, 'current': 100, 'high_52week': 200,
                  'currency': 'KRW'} for c in ['138040', '005930']]
    with patch.dict('os.environ', {}, clear=True):
        results, failed = report.run_market('kr', [(c['code'], c['name'], 'A', '') for c in snapshots],
                                           snapshots, tmp_path, CONFIG, AS_OF)
    assert failed and len(results) == 2
    assert all(r['metrics']['ttm'] is None and r['metrics']['status'] == '조회 실패' for r in results)
    text = (tmp_path / 'friendly_kr_dividend.csv').read_text(encoding='utf-8-sig')
    assert 'N/A' in text and 'DATA_GO_KR_SERVICE_KEY' in text


def test_old_ex_date_review_does_not_reclassify_record_date_events():
    h = korean_prices()
    h['dividend_date_basis'] = 'record_date'
    for e in h['dividends']:
        e.update(kind='unknown', currency='KRW')
    review = {'source': 'issuer', 'from': '2019-01-01', 'through': '2026-10-25', 'default_kind': 'regular'}
    assert metrics.calculate(h, 100, 'KRW', review, CONFIG['quality'])['ttm'] is None
    review['date_basis'] = 'record_date'
    assert metrics.calculate(h, 100, 'KRW', review, CONFIG['quality'])['ttm'] == 4


def test_foreign_route_does_not_require_korean_keys():
    with patch.dict('os.environ', {}, clear=True), patch.object(data, 'fetch_yahoo_history', return_value=sample()) as fetch:
        assert data.fetch_history('AAPL', 'us', CONFIG, AS_OF)['source'] == 'fixed sample'
    fetch.assert_called_once_with('AAPL', 'us', CONFIG, AS_OF)


def fiscal_sample(amounts=None):
    h = korean_prices()
    h['dividend_date_basis'] = 'record_date'
    for event in h['dividends']:
        event['currency'] = 'KRW'
    amounts = [4, 4, 4, 4, 4, 4] if amounts is None else amounts
    h['dart_annual'] = {str(year): {'amount': amount} for year, amount in zip(range(2020, 2026), amounts)}
    return h


def test_dart_fiscal_growth_and_cut_independent_of_event_years_and_price_coverage():
    h = fiscal_sample([1, 1.1, 1.21, 1.331, 1.4641, 1.61051])
    h['prices'] = [p for p in h['prices'] if p['date'] >= '2022-01-01']
    result = metrics.calculate(h, 100, 'KRW', {}, CONFIG['quality'], include_all=True)
    assert result['annual_dividends'][2025] == 1.61051
    assert result['record_year_dividends'][2025] == 4
    assert result['cagr_pct'] == pytest.approx(10)
    assert result['cut'] is False
    assert result['ttm'] == 4 and result['avg3_pct'] == 4
    assert result['avg5_pct'] is None
    assert 'DART 사업연도' in result['dividend_basis']


def test_dart_missing_year_does_not_block_yield_candidate():
    h = fiscal_sample([4, 4, None, 4, 4, 4])
    result = metrics.calculate(h, 80, 'KRW', {},
                               {**CONFIG['quality'], 'quote_tolerance_pct': 30}, include_all=True)
    assert result['annual_dividends'][2022] is None
    assert result['cagr_pct'] is None and result['cut'] is None
    assert result['ttm'] == 4 and result['ratio'] == 1.25
    assert metrics.is_candidate(result, -40, CONFIG['thresholds'])


def test_known_annual_decline_survives_unknown_ttm_without_fabricating_zero():
    h = fiscal_sample([4, 4, 4, 4, 4, 3])
    h['dividends'] = [e for e in h['dividends'] if e['date'] < '2025-01-01']
    result = metrics.calculate(h, 100, 'KRW', {}, CONFIG['quality'], include_all=True)
    assert result['ttm'] is None and result['yield_pct'] is None
    assert result['avg3_pct'] is not None
    assert result['cut'] is True
    assert result['cagr_pct'] == pytest.approx(((3 / 4) ** .2 - 1) * 100)


def test_domestic_ttm_decline_is_not_reported_as_annual_cut():
    h = fiscal_sample()
    h['dividends'][-1]['amount'] = 1
    result = metrics.calculate(h, 100, 'KRW', {}, CONFIG['quality'], include_all=True)
    assert result['ttm'] == 1
    assert result['cut'] is False and result['cagr_pct'] == 0


def test_domestic_regular_only_does_not_treat_dart_total_as_regular():
    h = fiscal_sample()
    result = metrics.calculate(h, 100, 'KRW', {}, CONFIG['quality'], include_all=False)
    assert result['ttm'] == 4 and result['avg3_pct'] == 4
    assert result['cut'] is None and result['cagr_pct'] is None
    assert all(v is None for v in result['annual_dividends'].values())
    assert any('정기·특별 분리 불가' in w for w in result['warnings'])


def test_domestic_split_holds_only_fiscal_growth_and_cut():
    h = fiscal_sample()
    h['splits'] = [{'date': '2023-01-01', 'ratio': 2}]
    result = metrics.calculate(h, 100, 'KRW', {}, CONFIG['quality'], include_all=True)
    assert result['ttm'] == 4 and result['avg3_pct'] == 4
    assert result['annual_dividends'][2025] == 4
    assert result['cagr_pct'] is None and result['cut'] is None
    assert result['status'] == '정상'


def test_bad_event_currency_does_not_erase_independent_dart_annual_metrics():
    h = fiscal_sample()
    h['dividends'][-1]['currency'] = 'USD'
    result = metrics.calculate(h, 100, 'KRW', {}, CONFIG['quality'], include_all=True)
    assert result['ttm'] is None and result['avg3_pct'] is None
    assert result['cagr_pct'] == 0 and result['cut'] is False
    assert result['annual_dividends'][2025] == 4


def test_dart_explicit_zero_can_confirm_annual_cut_without_a_growth_rate():
    h = fiscal_sample([4, 4, 4, 4, 4, 0])
    result = metrics.calculate(h, 100, 'KRW', {}, CONFIG['quality'], include_all=True)
    assert result['annual_dividends'][2025] == 0
    assert result['cut'] is True and result['cagr_pct'] is None
    assert result['ttm'] == 4 and result['avg3_pct'] == 4


def test_ktng_fiscal_2024_includes_next_year_final_dividend_without_changing_ttm_dates():
    h = fiscal_sample([4800, 4800, 5000, 5200, 5400, 6000])
    for p in h['prices']:
        p['close'] = 200000
    events = [('2019-12-31', 4400), ('2020-12-31', 4800), ('2021-12-31', 4800),
              ('2022-12-31', 5000), ('2023-06-30', 1200), ('2023-12-31', 4000),
              ('2024-06-30', 1200), ('2025-02-28', 4200), ('2025-08-22', 1400),
              ('2026-02-27', 4600), ('2026-08-21', 2000)]
    h['dividends'] = [{'date': d, 'amount': a, 'raw_amount': a, 'currency': 'KRW', 'kind': 'unknown'}
                      for d, a in events]
    h['annual_cross_checks'], h['dividend_warnings'] = kr.cross_check(
        h['dividends'], {int(y): info for y, info in h['dart_annual'].items()}, [])
    result = metrics.calculate(h, 200000, 'KRW', {}, CONFIG['quality'], include_all=True)
    assert result['annual_dividends'][2024] == 5400
    assert result['record_year_dividends'][2024] == 1200
    assert result['annual_dividends'][2025] == 6000
    assert result['record_year_dividends'][2025] == 5600
    assert result['ttm'] == 6600 and result['yield_pct'] == pytest.approx(3.3)
    assert result['avg3_pct'] is not None and result['avg5_pct'] is not None
    assert result['cagr_pct'] == pytest.approx(((6000 / 4800) ** .2 - 1) * 100)
    assert result['cut'] is False
    assert all('합계 차이' not in i for i in result['issues'])
    assert result['status'] == '정상'


def test_optional_dart_report_fetch_failure_preserves_official_events():
    def fake_dart(endpoint, key, **params):
        if endpoint == 'company':
            return {'status': '000', 'corp_code': CORP, 'stock_code': '138040',
                    'jurir_no': CRNO, 'acc_mt': '12'}
        raise ValueError('OpenDART unavailable')
    with patch.dict('os.environ', KEYS), patch.object(kr, 'corporation_codes', return_value={'138040': CORP}), \
         patch.object(kr, 'dart_json', side_effect=fake_dart), \
         patch.object(kr, 'fetch_fsc_rows', return_value=[row()]):
        h = kr.fetch_korean_history('138040', CONFIG, AS_OF, korean_prices)
    assert h['dividends'][0]['amount'] == 1350
    assert not h['dividend_issues']
    assert all(info['reason_code'] == 'fetch_failed' for info in h['dart_annual'].values())
