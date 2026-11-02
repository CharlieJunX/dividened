import json
import math
from pathlib import Path
from datetime import date

KINDS = {'GENERAL', 'BANK', 'INSURER', 'REIT', 'OTHER_FINANCIAL', 'UNKNOWN'}
METRICS = {'per', 'pbr', 'fcf_yield', 'ev_ebitda', 'p_affo', 'p_ffo', 'unclassified'}


def number(value, lower=0, upper=100):
    if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError('Investment configuration contains an invalid number')


def load_config(path=None):
    path = Path(path) if path else Path(__file__).resolve().parent.parent / 'investment_config.json'
    cfg = json.loads(path.read_text(encoding='utf-8-sig'))
    if type(cfg['enabled']) is not bool:
        raise ValueError('enabled must be boolean')
    weights = cfg['domain_weights']
    if set(weights) != {'shareholder', 'fundamental', 'valuation', 'growth', 'price_risk'}:
        raise ValueError('Exactly five score domains required')
    for v in weights.values():
        number(v, .01)
    if sum(weights.values()) != 100:
        raise ValueError('Domain weights must total 100')
    for group, maximum in [('grade_points', weights['shareholder']), ('fundamental_points', weights['fundamental'])]:
        for v in cfg[group].values():
            number(v, 0, maximum)
    if set(cfg['fundamental_points']) != {'NORMAL', 'WARNING', 'HIGH_RISK'}:
        raise ValueError('INSUFFICIENT_DATA must not have a numerical score')
    number(cfg['high_risk_cap'])
    if set(cfg['grades']) != {'S', 'A', 'B', 'C', 'D'} or cfg['grades']['D'] != 0:
        raise ValueError('Investment grades must cover S/A/B/C/D')
    floors = [cfg['grades'][k] for k in ('S', 'A', 'B', 'C', 'D')]
    for v in floors:
        number(v)
    if any(a <= b for a, b in zip(floors, floors[1:])):
        raise ValueError('Grade thresholds must strictly descend')
    if not 0 < cfg['coverage']['provisional'] <= cfg['coverage']['regular'] <= 100:
        raise ValueError('Invalid coverage thresholds')
    val = cfg['valuation']
    if type(val['months']) is not int or type(val['min_months']) is not int or not 36 <= val['min_months'] <= val['months'] <= 60:
        raise ValueError('Historical valuation requires 36 to 60 months')
    for k in ('max_price_age_days', 'max_financial_age_days', 'max_shares_age_days'):
        number(val[k], 1, 550)
    for v in val['max_multiples'].values():
        number(v, 1, 10000)
    number(val['structural_change_ratio'], 1, 100)
    if set(cfg['industries']) != KINDS:
        raise ValueError('All industry policies required')
    for kind, policy in cfg['industries'].items():
        if set(policy['valuation']) - METRICS:
            raise ValueError(f'Unknown valuation metric for {kind}')
        for domain in ('valuation', 'growth'):
            if sum(policy[domain].values()) != weights[domain]:
                raise ValueError(f'{kind} {domain} weights must match domain allocation')
            for v in policy[domain].values():
                number(v, .01, weights[domain])
        if kind in {'BANK', 'INSURER', 'OTHER_FINANCIAL'} and set(policy['valuation']) - {'per', 'pbr'}:
            raise ValueError('Financial companies require a financial valuation policy')
        if kind == 'REIT' and set(policy['valuation']) - {'p_affo', 'p_ffo'}:
            raise ValueError('REIT uses FFO/AFFO valuation')
    risk = cfg['price_risk']
    for k in ('min_observations', 'min_span_days', 'max_gap_days', 'annualization_days'):
        if type(risk[k]) is not int or risk[k] < 1:
            raise ValueError('Invalid price risk window')
    for bands in [risk['default'], *risk['overrides'].values()]:
        for key in ('volatility', 'mdd'):
            a, b = bands[key]
            number(a, 0, 10000)
            number(b, 0, 10000)
            if a >= b:
                raise ValueError('Risk thresholds must increase')
    number(cfg['growth']['full_score_cagr_pct'], .01, 10000)
    number(cfg['growth']['one_off_jump_pct'], 1, 10000)
    number(cfg['growth']['one_off_max_fraction'], 0, 1)
    n = cfg['notifications']
    if type(n['max_candidates']) is not int or n['max_candidates'] < 1:
        raise ValueError('max_candidates must be a positive integer')
    if any(type(n[k]) is not bool for k in ('detail', 'events_candidate_only')):
        raise ValueError('Notification switches must be boolean')
    number(n['score_drop'], .01)
    for review in cfg['securities'].values():
        if not isinstance(review, dict) or not review.get('source') or not review.get('reviewed_on'):
            raise ValueError('Security valuation reviews require source and reviewed_on')
        date.fromisoformat(review['reviewed_on'])
    return cfg
