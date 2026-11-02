from datetime import date
from math import log, sqrt, isfinite
from statistics import stdev
from dividend_data import shift_months
from valuation.metrics import price_at


def calculate(history, as_of, market, industry, config):
    policy = config['price_risk']
    start = shift_months(as_of, -12).isoformat()
    rows = sorted((p for p in history.get('prices', []) if start <= p['date'] <= str(as_of)), key=lambda p: p['date'])
    reason = ''
    if (len(rows) < policy['min_observations'] or not rows
            or (date.fromisoformat(rows[-1]['date']) - date.fromisoformat(rows[0]['date'])).days < policy['min_span_days']):
        reason = '최근 1년 가격 이력 부족'
    if len({p['date'] for p in rows}) != len(rows):
        reason = '가격 날짜 중복'
    if any(not isinstance(p.get('close'), (int, float)) or isinstance(p.get('close'), bool) or not isfinite(p['close']) or p['close'] <= 0 for p in rows):
        reason = '일별 가격 누락 또는 비정상 값'
    if any((date.fromisoformat(b['date']) - date.fromisoformat(a['date'])).days > policy['max_gap_days'] for a, b in zip(rows, rows[1:])):
        reason = '가격 이력 공백'
    if price_at(history, as_of, config['valuation']).value is None:
        reason = '최신 가격 지연 또는 조정 기준 미확인'
    bands = policy['overrides'].get(f'{market}:{industry}', policy['overrides'].get(market, policy['default']))
    output = {}
    volatility, mdd = None, None
    if not reason:
        # Yahoo Close already neutralizes splits. Raw inputs are normalized to the same end basis.
        from valuation.metrics import split_ratio
        prices = [p['close'] / split_ratio(history, p['date'], str(as_of)) if history.get('basis') == 'raw' else p['close'] for p in rows]
        returns = [log(b / a) for a, b in zip(prices, prices[1:])]
        if any(max(b / a, a / b) > 4 for a, b in zip(prices, prices[1:])):
            return {key: {'value': None, 'score': None, 'weight': config['domain_weights']['price_risk'] / 2,
                          'reason': '주가 급변/분할 기준 확인 필요', 'comparison_policy': bands,
                          'start': rows[0]['date'], 'end': rows[-1]['date'], 'source': history.get('source'),
                          'observations': len(rows)} for key in ('volatility', 'mdd')}
        volatility = stdev(returns) * sqrt(policy['annualization_days']) * 100
        peak, drawdown = prices[0], 0
        for price in prices:
            peak = max(peak, price)
            drawdown = min(drawdown, price / peak - 1)
        mdd = drawdown * 100
    for key, value in [('volatility', volatility), ('mdd', mdd)]:
        weight = config['domain_weights']['price_risk'] / 2
        good, bad = bands[key]
        fraction = max(0, min(1, (bad - abs(value)) / (bad - good))) if value is not None else None
        output[key] = {'value': value, 'score': fraction * weight if fraction is not None else None,
                       'weight': weight, 'reason': reason, 'comparison_policy': bands,
                       'start': rows[0]['date'] if rows else None, 'end': rows[-1]['date'] if rows else None,
                       'source': history.get('source'), 'observations': len(rows)}
    return output
