"""Synthetic financial histories; no API credentials or live messages."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
import math
import pytest
from fundamentals.models import Fact
from fundamentals.storage import Store
from investment.config import load_config
from valuation.metrics import calculate_point, price_at
from valuation.history import analyze, month_ends, quantile, percentile

AS_OF = date(2026, 10, 28)
CONFIG = load_config()
REVIEW = {'source': 'https://example.invalid/SYNTHETIC', 'reviewed_on': str(AS_OF),
          'common_shares_verified': True, 'common_equity_verified': True,
          'ev_adjustments_verified': True, 'ev_definition': 'cap+debt-cash+explicit_adjustments',
          'common_earnings_verified': True, 'ffo_definition': 'SYNTHETIC common attributable FFO/AFFO'}


def fact(metric, value, start, end, **kwargs):
    options = dict(filed=(date.fromisoformat(end) + timedelta(days=45)).isoformat(),
                   unit='USD/shares' if metric == 'eps' else 'shares' if metric == 'common_shares' else 'USD',
                   scope='issuer' if metric == 'common_shares' else 'consolidated', standard='us-gaap',
                   source='https://example.invalid/SYNTHETIC', accession=end, tag=metric, eps_verified=metric == 'eps')
    options.update(kwargs)
    return Fact(metric, value, start, end, **options)


def financials():
    fs = []
    ends, starts = ['03-31', '06-30', '09-30', '12-31'], ['01-01', '04-01', '07-01', '10-01']
    flows = {'revenue': 100, 'net_income': 20, 'eps': 1, 'ocf': 30, 'capex': 5, 'ebitda': 40,
             'affo': 15, 'ffo': 18, 'rental_income': 75, 'insurance_income': 10}
    balances = {'common_shares': 100, 'common_equity': 1000, 'equity_parent': 1000, 'preferred_equity': 0,
                'equity': 1000, 'total_debt': 400, 'cash': 100, 'ev_adjustments': 0}
    for year in range(2018, 2027):
        factor = 1.2 ** (year - 2018)
        for i in range(4):
            start, end = f'{year}-{starts[i]}', f'{year}-{ends[i]}'
            for k, v in flows.items():
                fs.append(fact(k, v * factor, start, end))
            for k, v in balances.items():
                fs.append(fact(k, v, None, end))
        for k, v in flows.items():
            fs.append(fact(k, v * factor * 4, f'{year}-01-01', f'{year}-12-31'))
    return fs


def prices(current=70):
    rows, day = [], date(2019, 1, 1)
    while day <= AS_OF:
        if day.weekday() < 5:
            rows.append({'date': str(day), 'close': current if day == AS_OF else 100})
        day += timedelta(days=1)
    return {'prices': rows, 'currency': 'USD', 'basis': 'split_adjusted', 'splits': [],
            'as_of': str(AS_OF), 'source': 'SYNTHETIC PRICE SERIES'}


def test_current_valuation_formulas_and_provenance():
    r = calculate_point(financials(), prices(), AS_OF, 'GENERAL', CONFIG, REVIEW)
    multiplier = 2 * (1.2 ** 7 + 1.2 ** 8)
    assert r['metrics']['per']['value'] == pytest.approx(70 / multiplier)
    assert r['metrics']['pbr']['value'] == 7
    assert r['metrics']['fcf_yield']['value'] == pytest.approx(25 * multiplier / 7000 * 100)
    assert r['metrics']['ev_ebitda']['value'] == pytest.approx(7300 / (40 * multiplier))
    assert r['metrics']['per']['evidence'][0]['filed'] <= str(AS_OF)


@pytest.mark.parametrize('metric,target,bad', [('eps', 'per', 0), ('eps', 'per', -1),
                                           ('common_equity', 'pbr', 0), ('common_equity', 'pbr', -1),
                                           ('ebitda', 'ev_ebitda', -1), ('ebitda', 'ev_ebitda', 0)])
def test_nonpositive_denominators_are_na(metric, target, bad):
    fs = [replace(f, value=bad) if f.metric == metric else f for f in financials()]
    assert calculate_point(fs, prices(), AS_OF, 'GENERAL', CONFIG, REVIEW)['metrics'][target]['value'] is None


def test_negative_fcf_yield_preserved_and_never_cheap():
    fs = [replace(f, value=-10) if f.metric == 'ocf' else f for f in financials()]
    r = analyze(fs, prices(), AS_OF, 'GENERAL', CONFIG, REVIEW)
    assert r['metrics']['fcf_yield']['value'] < 0
    assert r['metrics']['fcf_yield']['percentile'] is None
    assert not r['historically_cheap']


def test_extreme_multiple_and_missing_adjustments_never_become_cheap():
    fs = [replace(f, value=.00001) if f.metric == 'eps' else f for f in financials()]
    extreme = calculate_point(fs, prices(), AS_OF, 'GENERAL', CONFIG, REVIEW)['metrics']['per']
    assert extreme['value'] > CONFIG['valuation']['max_multiples']['per'] and not extreme['eligible']
    fs = [f for f in financials() if f.metric != 'ev_adjustments']
    assert calculate_point(fs, prices(), AS_OF, 'GENERAL', CONFIG, REVIEW)['metrics']['ev_ebitda']['value'] is None


def test_financial_and_reit_policies_are_distinct():
    for kind in ('BANK', 'INSURER', 'OTHER_FINANCIAL'):
        assert set(calculate_point(financials(), prices(), AS_OF, kind, CONFIG, REVIEW)['metrics']) == {'per', 'pbr'}
    reit = calculate_point(financials(), prices(), AS_OF, 'REIT', CONFIG, REVIEW)
    assert set(reit['metrics']) == {'p_affo', 'p_ffo'}
    assert all(m['value'] > 0 for m in reit['metrics'].values())
    reit = calculate_point(financials(), prices(), AS_OF, 'REIT', CONFIG, {})
    assert all(m['value'] is None for m in reit['metrics'].values())


def test_historical_months_percentile_quantiles_and_minimum_coverage():
    dates = month_ends(AS_OF)
    assert len(dates) == 60 and dates[0] == date(2021, 10, 31) and dates[-1] == date(2026, 9, 30)
    assert quantile([10, 20, 30, 40, 50], .2) == 18
    assert percentile([5, 5, 5], 5) == .5
    r = analyze(financials(), prices(), AS_OF, 'GENERAL', CONFIG, REVIEW)
    assert r['metrics']['per']['valid_months'] == 60
    # Recently disclosed share counts exceed the 120-day limit in some month-end windows.
    assert all(m['valid_months'] >= 36 for m in r['metrics'].values())
    assert r['metrics']['pbr']['valid_months'] == 44
    assert r['metrics']['per']['percentile'] == 0
    assert r['historically_cheap']
    short = prices()
    short['prices'] = [p for p in short['prices'] if p['date'] >= '2024-01-01']
    r = analyze(financials(), short, AS_OF, 'GENERAL', CONFIG, REVIEW)
    assert r['metrics']['per']['valid_months'] < 36
    assert r['metrics']['per']['percentile'] is None


def test_history_uses_then_public_earnings_not_current_earnings():
    fs = financials()
    day = date(2022, 8, 31)
    old = calculate_point(fs, prices(), day, 'GENERAL', CONFIG, REVIEW)
    changed = [replace(f, value=f.value * 100) if f.metric == 'eps' and f.filed > str(day) else f for f in fs]
    assert calculate_point(changed, prices(), day, 'GENERAL', CONFIG, REVIEW)['metrics']['per'] == old['metrics']['per']
    assert old['metrics']['per']['value'] != calculate_point(fs, prices(), AS_OF, 'GENERAL', CONFIG, REVIEW)['metrics']['per']['value']


def test_future_restatement_changes_only_subsequent_dates():
    fs = financials()
    original = next(f for f in fs if f.metric == 'eps' and f.start == '2025-10-01')
    amendment = replace(original, value=original.value / 2, filed='2026-10-01', accession='restated')
    before = date(2026, 9, 30)
    assert calculate_point(fs, prices(), before, 'GENERAL', CONFIG, REVIEW)['metrics']['per'] == calculate_point(fs + [amendment], prices(), before, 'GENERAL', CONFIG, REVIEW)['metrics']['per']
    assert calculate_point(fs + [amendment], prices(), AS_OF, 'GENERAL', CONFIG, REVIEW)['metrics']['per']['value'] > calculate_point(fs, prices(), AS_OF, 'GENERAL', CONFIG, REVIEW)['metrics']['per']['value']


def test_currency_and_share_basis_must_be_verified():
    fs = [replace(f, unit='EUR/shares') if f.metric == 'eps' else f for f in financials()]
    assert calculate_point(fs, prices(), AS_OF, 'GENERAL', CONFIG, REVIEW)['metrics']['per']['value'] is None
    r = calculate_point(financials(), prices(), AS_OF, 'GENERAL', CONFIG, {})
    assert r['metrics']['pbr']['value'] is None
    fs = [f for f in financials() if f.metric != 'common_equity']
    assert calculate_point(fs, prices(), AS_OF, 'GENERAL', CONFIG, REVIEW)['metrics']['pbr']['value'] == 7
    fs = [f for f in fs if f.metric != 'preferred_equity']
    assert calculate_point(fs, prices(), AS_OF, 'GENERAL', CONFIG, REVIEW)['metrics']['pbr']['value'] is None


def test_stock_split_raw_price_reconstruction_and_unsafe_eps_rejected():
    history = prices()
    history['splits'] = [{'date': '2026-09-01', 'ratio': 2}]
    assert price_at(history, date(2026, 8, 31), CONFIG['valuation']).value == 200
    r = calculate_point(financials(), history, AS_OF, 'GENERAL', CONFIG, REVIEW)
    assert r['metrics']['per']['value'] is None
    reviewed = {**REVIEW, 'eps_split_basis_verified': True, 'eps_basis': 'period_end'}
    assert calculate_point(financials(), history, AS_OF, 'GENERAL', CONFIG, reviewed)['metrics']['per']['value'] is not None


def test_durable_month_cache_not_invalidated_by_future_filings(tmp_path):
    store = Store(tmp_path / 'state.sqlite3')
    fs = financials()
    analyze(fs, prices(), AS_OF, 'BANK', CONFIG, REVIEW, store, 'us:SAMPLE')
    count = store.db.execute('SELECT COUNT(*) FROM valuation_months').fetchone()[0]
    assert count == 60
    # This filing is after every stored month end and cannot change those values/cache entries.
    fs += [fact('eps', 1, '2026-07-01', '2026-09-30', filed='2026-11-10')]
    analyze(fs, prices(), AS_OF, 'BANK', CONFIG, REVIEW, store, 'us:SAMPLE')
    assert store.db.execute('SELECT COUNT(*) FROM valuation_months').fetchone()[0] == count
    store.close()


def test_sec_alias_history_retains_original_known_before_later_restatement():
    from fundamentals.adapters.sec import parse_facts
    old = {'start': '2024-01-01', 'end': '2024-12-31', 'filed': '2025-02-01',
           'form': '10-K', 'accn': 'original', 'val': 100}
    new = {**old, 'filed': '2026-02-01', 'accn': 'restated', 'val': 80}
    payload = {'cik': 1, 'facts': {'us-gaap': {
        'RevenueFromContractWithCustomerExcludingAssessedTax': {'units': {'USD': [new]}},
        'Revenues': {'units': {'USD': [old]}}}}}
    fs = parse_facts(payload, AS_OF, retain_aliases=True)
    from fundamentals.periods import versions
    assert len(fs) == 2
    assert versions(fs, date(2025, 3, 1))[0].value == 100
    assert versions(fs, AS_OF)[0].value == 80
