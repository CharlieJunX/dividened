from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
import pytest
from investment.config import load_config
from investment.scoring import evaluate
from investment.growth import calculate as growth
from investment.price_risk import calculate as risk
from investment.events import detect
from test_valuation import AS_OF, CONFIG, REVIEW, financials, prices


def inputs(kind='GENERAL'):
    valuation = {'metrics': {k: {'value': 10, 'percentile': 1 if k == 'fcf_yield' else 0, 'reason': ''}
                             for k in CONFIG['industries'][kind]['valuation']}, 'historically_cheap': True}
    g = {k: {'score': w, 'weight': w} for k, w in CONFIG['industries'][kind]['growth'].items()}
    p = {k: {'score': 5, 'weight': 5} for k in ('volatility', 'mdd')}
    return valuation, g, p


def test_full_score_grade_and_high_risk_cap():
    v, g, p = inputs()
    r = evaluate('A', {'status': 'NORMAL'}, v, g, p, 'GENERAL', CONFIG)
    assert r['adjusted_score'] == 100 and r['grade'] == 'S' and r['coverage_pct'] == 100
    r = evaluate('A', {'status': 'HIGH_RISK'}, v, g, p, 'GENERAL', CONFIG)
    assert r['raw_score'] == 75 and r['normalized_score'] == 75
    assert r['adjusted_score'] == 59 and r['grade'] == 'B' and r['risk_cap_applied']


def test_missing_is_none_not_zero_and_core_exclusion():
    v, g, p = inputs()
    r = evaluate('A', {'status': 'INSUFFICIENT_DATA'}, v, g, p, 'GENERAL', CONFIG)
    assert r['domains']['fundamental']['score'] is None
    assert r['adjusted_score'] is None and r['coverage_pct'] == 75 and 'fundamental' in r['missing_core']
    r = evaluate('', {'status': 'NORMAL'}, v, g, p, 'GENERAL', CONFIG)
    assert r['domains']['shareholder']['score'] is None and not r['rank_eligible']


def test_coverage_regular_provisional_unrated_and_normalization():
    v, g, p = inputs()
    v['metrics']['pbr']['percentile'] = None
    r = evaluate('A', {'status': 'NORMAL'}, v, g, p, 'GENERAL', CONFIG)
    assert r['coverage_pct'] == 96 and r['evaluation_status'] == 'REGULAR'
    assert r['raw_score'] == 96 and r['normalized_score'] == 100
    v['metrics']['fcf_yield']['percentile'] = None
    r = evaluate('A', {'status': 'NORMAL'}, v, g, p, 'GENERAL', CONFIG)
    assert r['coverage_pct'] == 87 and r['evaluation_status'] == 'PROVISIONAL'
    g['revenue']['score'] = None
    p['mdd']['score'] = None
    r = evaluate('A', {'status': 'NORMAL'}, v, g, p, 'GENERAL', CONFIG)
    assert r['coverage_pct'] == 77 and r['adjusted_score'] is None


@pytest.mark.parametrize('kind', ['BANK', 'INSURER', 'REIT', 'OTHER_FINANCIAL'])
def test_sector_exclusions_do_not_reduce_coverage(kind):
    v, g, p = inputs(kind)
    r = evaluate('A', {'status': 'NORMAL'}, v, g, p, kind, CONFIG)
    assert r['coverage_pct'] == 100 and r['adjusted_score'] == 100
    assert 'valuation:fcf_yield' not in r['leaves']


def test_negative_fcf_cannot_get_high_valuation_points():
    v, g, p = inputs()
    v['metrics']['fcf_yield']['value'] = -5
    r = evaluate('A', {'status': 'NORMAL'}, v, g, p, 'GENERAL', CONFIG)
    assert r['leaves']['valuation:fcf_yield']['score'] is None


def test_growth_three_years_four_values_and_continuity():
    r = growth(financials(), prices(), AS_OF, 'GENERAL', CONFIG, REVIEW)
    assert r['revenue']['cagr_pct'] == pytest.approx(20)
    assert r['revenue']['score'] == pytest.approx(5)
    assert len(r['revenue']['evidence']) == 4
    fs = [f for f in financials() if not (f.metric == 'revenue' and f.end == '2023-12-31' and f.days > 330)]
    assert growth(fs, prices(), AS_OF, 'GENERAL', CONFIG, REVIEW)['revenue']['score'] is None


def test_loss_turnaround_and_one_off_growth_are_not_high_cagr():
    fs = [replace(f, value=-1) if f.metric == 'eps' and f.end == '2022-12-31' else f for f in financials()]
    assert growth(fs, prices(), AS_OF, 'GENERAL', CONFIG, REVIEW)['eps']['score'] is None
    fs = [replace(f, value=f.value * 10) if f.metric == 'revenue' and f.days > 330 and f.end == '2025-12-31' else f for f in financials()]
    g = growth(fs, prices(), AS_OF, 'GENERAL', CONFIG, REVIEW)['revenue']
    assert g['score'] <= 2.5 and '급증' in g['reason']


def test_price_risk_does_not_reward_drawdowns_and_checks_gaps():
    calm = risk(prices(100), AS_OF, 'us', 'GENERAL', CONFIG)
    crashed = risk(prices(40), AS_OF, 'us', 'GENERAL', CONFIG)
    assert calm['mdd']['score'] == 5
    assert crashed['mdd']['value'] == -60 and crashed['mdd']['score'] == 0
    assert crashed['volatility']['score'] < calm['volatility']['score']
    short = prices()
    short['prices'] = short['prices'][-50:]
    assert risk(short, AS_OF, 'us', 'GENERAL', CONFIG)['mdd']['score'] is None


def test_score_events_and_missing_comparisons():
    old = {'score': {'grade': 'A', 'adjusted_score': 80}, 'fundamental_status': 'WARNING',
           'candidate': True, 'valuation': {'historically_cheap': False}}
    new = deepcopy(old)
    new['score'].update(grade='S', adjusted_score=90)
    assert detect(new, old, CONFIG) == ['A_TO_S']
    assert detect(new, None, CONFIG) == []
    new['score'].update(grade='C', adjusted_score=65)
    new['fundamental_status'] = 'HIGH_RISK'
    new['valuation']['historically_cheap'] = True
    assert set(detect(new, old, CONFIG)) == {'SCORE_DROP', 'HIGH_RISK_ENTRY', 'DIVIDEND_AND_VALUE_ENTRY'}
    old['score']['adjusted_score'] = None
    assert 'SCORE_DROP' not in detect(new, old, CONFIG)


def test_invalid_policies_and_score_overflow_rejected(tmp_path):
    cfg = deepcopy(CONFIG)
    cfg['industries']['BANK']['valuation']['fcf_yield'] = 1
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(cfg))
    with pytest.raises(ValueError):
        load_config(path)
    v, g, p = inputs()
    g['revenue']['score'] = 500
    with pytest.raises(ValueError):
        evaluate('A', {'status': 'NORMAL'}, v, g, p, 'GENERAL', CONFIG)


@pytest.mark.parametrize('points,grade', [(100, 'S'), (85, 'S'), (70, 'A'), (55, 'B'), (40, 'C'), (0, 'D')])
def test_exact_grade_boundaries(points, grade):
    cfg = deepcopy(CONFIG)
    fraction = points / 100
    cfg['grade_points']['A'] = 25 * fraction
    cfg['fundamental_points']['NORMAL'] = 25 * fraction
    v, g, p = inputs()
    for k, m in v['metrics'].items():
        m['percentile'] = fraction if k == 'fcf_yield' else 1 - fraction
    for m in [*g.values(), *p.values()]:
        m['score'] = m['weight'] * fraction
    result = evaluate('A', {'status': 'NORMAL'}, v, g, p, 'GENERAL', cfg)
    assert result['adjusted_score'] == points and result['grade'] == grade


def test_exact_coverage_boundaries():
    v, g, p = inputs()
    p['volatility']['score'] = None
    assert evaluate('A', {'status': 'NORMAL'}, v, g, p, 'GENERAL', CONFIG)['evaluation_status'] == 'REGULAR'
    for m in g.values():
        m['score'] = None
    result = evaluate('A', {'status': 'NORMAL'}, v, g, p, 'GENERAL', CONFIG)
    assert result['coverage_pct'] == 80 and result['evaluation_status'] == 'PROVISIONAL'
    assert result['normalized_score'] == 100
