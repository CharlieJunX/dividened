from datetime import date, timedelta
from statistics import median
from dividend_data import shift_months
from .metrics import calculate_point
from .metrics import price_at
from fundamentals.periods import versions
from fundamentals.storage import digest


def month_ends(as_of, count=60):
    latest = as_of.replace(day=1) - timedelta(days=1)
    return [shift_months(latest.replace(day=1), -i + 1) - timedelta(days=1)
            for i in range(count - 1, -1, -1)]


def quantile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * fraction
    left = int(position)
    right = min(left + 1, len(values) - 1)
    return values[left] + (values[right] - values[left]) * (position - left)


def percentile(values, current):
    # Midrank gives a constant distribution 50%, rather than declaring it cheap.
    return (sum(v < current for v in values) + .5 * sum(v == current for v in values)) / len(values)


def band(cheap_percentile):
    if cheap_percentile <= .2:
        return 'HISTORICALLY_CHEAP'
    if cheap_percentile <= .4:
        return 'SOMEWHAT_CHEAP'
    if cheap_percentile <= .6:
        return 'NEUTRAL'
    if cheap_percentile <= .8:
        return 'SOMEWHAT_EXPENSIVE'
    return 'HISTORICALLY_EXPENSIVE'


def analyze(facts, history, as_of, industry, config, review=None, store=None, symbol=''):
    review = review or {}
    current = calculate_point(facts, history, as_of, industry, config, review)
    # Content-addressed cache: corrections change the key; age/staleness is recalculated each run.
    signature = digest({'facts': [f.dump() for f in facts], 'history': history,
                        'policy': config['valuation'], 'industry': industry, 'review': review})
    samples = []
    for day in month_ends(as_of, config['valuation']['months']):
        # Future filings and daily price append operations must not invalidate old months.
        key = digest({'facts': [f.dump() for f in versions(facts, day)],
                      'price': price_at(history, day, config['valuation']).dump(),
                      'splits': [s for s in history.get('splits', []) if s['date'] <= str(day)],
                      'policy': config['valuation'], 'industry': industry, 'review': review})
        point = store.valuation_month(symbol, str(day), key) if store else None
        if point is None:
            point = calculate_point(facts, history, day, industry, config, review)
            if store:
                store.valuation_month(symbol, str(day), key, point)
        samples.append(point)
    metrics, warnings = {}, []
    for metric, observation in current['metrics'].items():
        valid = [(r['date'], r['metrics'][metric]['value']) for r in samples
                 if r['metrics'][metric]['value'] is not None
                 and r['metrics'][metric].get('eligible', True)
                 and (metric != 'fcf_yield' or r['metrics'][metric]['value'] > 0)]
        values = [v for _, v in valid]
        latest36 = [v for day, v in valid if day >= shift_months(as_of.replace(day=1), -36).isoformat()]
        cur = observation['value']
        usable = cur is not None and observation.get('eligible', True) and (metric != 'fcf_yield' or cur > 0)
        p = percentile(values, cur) if usable and len(values) >= config['valuation']['min_months'] else None
        cheap = (1 - p if metric == 'fcf_yield' else p) if p is not None else None
        reason = observation['reason']
        if len(values) < config['valuation']['min_months']:
            reason += f'; 유효 월말 {len(values)}개: 최소 {config["valuation"]["min_months"]}개 필요'
        metrics[metric] = {**observation, 'percentile': p, 'cheap_percentile': cheap,
                           'median5': median(values) if len(values) >= config['valuation']['min_months'] else None,
                           'q20': quantile(values, .2) if len(values) >= config['valuation']['min_months'] else None,
                           'q80': quantile(values, .8) if len(values) >= config['valuation']['min_months'] else None,
                           'median3': median(latest36) if len(latest36) == 36 else None,
                           'valid_months': len(values), 'reason': reason.strip('; '),
                           'band': band(cheap) if cheap is not None else 'N/A',
                           'excluded': [{'date': r['date'], 'value': r['metrics'][metric]['value'],
                                         'reason': r['metrics'][metric]['reason'] or '음수 FCF Yield 제외'}
                                        for r in samples if r['metrics'][metric]['value'] is None
                                        or not r['metrics'][metric].get('eligible', True)
                                        or (metric == 'fcf_yield' and r['metrics'][metric]['value'] <= 0)]}
    # Structural earnings shifts: annotate, rather than invent a normalized profit.
    per_samples = [(p['date'], p['price']['value'] / p['metrics']['per']['value']) for p in samples
                   if 'per' in p['metrics'] and p['price']['value'] is not None and p['metrics']['per']['value']]
    has_split = any(str(month_ends(as_of, config['valuation']['months'])[0]) <= s['date'] <= str(as_of) for s in history.get('splits', []))
    if has_split:
        warnings.append('분할 포함 구간: 원시 EPS 수준으로 구조 변화 여부를 단정하지 않음')
    if len(per_samples) >= 36 and not has_split:
        old, new = median(v for _, v in per_samples[:18]), median(v for _, v in per_samples[-18:])
        ratio = config['valuation']['structural_change_ratio']
        if old > 0 and new > 0 and max(new / old, old / new) >= ratio:
            warnings.append('이익 수준 구조 변화 가능성: 과거 PER 분포 해석 주의')
    cheap_values = [m['cheap_percentile'] for m in metrics.values() if m['cheap_percentile'] is not None]
    total_weight = sum(config['industries'][industry]['valuation'].values())
    assessed_weight = sum(config['industries'][industry]['valuation'][k] for k, m in metrics.items() if m['cheap_percentile'] is not None)
    weighted = (sum(m['cheap_percentile'] * config['industries'][industry]['valuation'][k]
                    for k, m in metrics.items() if m['cheap_percentile'] is not None) / assessed_weight) if assessed_weight else None
    return {'as_of': str(as_of), 'current': current, 'metrics': metrics, 'samples': samples,
            'historically_cheap': weighted is not None and weighted <= .2 and assessed_weight == total_weight,
            'band': band(weighted) if weighted is not None else 'N/A',
            'valuation_coverage': assessed_weight / total_weight * 100,
            'warnings': warnings + current['warnings'], 'signature': signature}
