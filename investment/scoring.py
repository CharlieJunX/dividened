from math import isfinite


def evaluate(grade, fundamental, valuation, growth, price_risk, industry, config):
    weights = config['domain_weights']
    leaves = {}
    leaves['shareholder'] = {'score': config['grade_points'].get(grade), 'weight': weights['shareholder'],
                             'reason': '' if grade in config['grade_points'] else '주주친화 등급 없음/미등록'}
    state = fundamental.get('status', 'INSUFFICIENT_DATA')
    leaves['fundamental'] = {'score': config['fundamental_points'].get(state), 'weight': weights['fundamental'],
                            'reason': '' if state in config['fundamental_points'] else '펀더멘털 평가 불가'}
    for metric, weight in config['industries'][industry]['valuation'].items():
        m = valuation.get('metrics', {}).get(metric, {})
        p = m.get('percentile')
        value = m.get('value')
        valid = (type(p) in (int, float) and isfinite(p) and 0 <= p <= 1
                 and type(value) in (int, float) and isfinite(value) and value > 0 and m.get('eligible', True))
        score = weight * (p if metric == 'fcf_yield' else 1 - p) if valid else None
        leaves['valuation:' + metric] = {'score': score, 'weight': weight, 'reason': m.get('reason', '지표 또는 역사 분포 미확인')}
    for metric, weight in config['industries'][industry]['growth'].items():
        leaves['growth:' + metric] = {**growth.get(metric, {'score': None, 'reason': '성장 자료 없음'}), 'weight': weight}
    for metric in ('volatility', 'mdd'):
        leaves['price_risk:' + metric] = {**price_risk.get(metric, {'score': None, 'reason': '가격 자료 없음'}), 'weight': weights['price_risk'] / 2}
    domains = {}
    for domain, maximum in weights.items():
        parts = [v for k, v in leaves.items() if k.split(':')[0] == domain]
        available = sum(v['weight'] for v in parts if v['score'] is not None)
        earned = sum(v['score'] for v in parts if v['score'] is not None)
        if any(v['score'] is not None and (type(v['score']) not in (int, float) or not isfinite(v['score']) or not 0 <= v['score'] <= v['weight']) for v in parts):
            raise ValueError('Score outside metric allocation')
        domains[domain] = {'score': earned if available else None, 'available_weight': available, 'weight': maximum}
    available = sum(d['available_weight'] for d in domains.values())
    earned = sum(d['score'] for d in domains.values() if d['score'] is not None)
    coverage = available  # Every industry is configured to allocate exactly 100 points.
    missing_core = [k for k in ('shareholder', 'fundamental', 'valuation') if domains[k]['available_weight'] == 0]
    eligible = coverage >= config['coverage']['provisional'] and not missing_core and industry != 'UNKNOWN'
    status = 'REGULAR' if eligible and coverage >= config['coverage']['regular'] else 'PROVISIONAL' if eligible else 'UNRATED'
    normalized = min(100, earned / available * 100) if eligible else None
    adjusted = min(normalized, config['high_risk_cap']) if normalized is not None and state == 'HIGH_RISK' else normalized
    adjusted = round(adjusted, 2) if adjusted is not None else None
    investment_grade = next((g for g, floor in sorted(config['grades'].items(), key=lambda item: -item[1])
                             if adjusted >= floor), None) if adjusted is not None else None
    return {'raw_score': earned if available else None, 'normalized_score': normalized, 'adjusted_score': adjusted,
            'grade': investment_grade, 'coverage_pct': coverage, 'evaluation_status': status,
            'rank_eligible': bool(eligible), 'missing_core': missing_core, 'domains': domains, 'leaves': leaves,
            'risk_cap_applied': normalized is not None and adjusted < normalized and state == 'HIGH_RISK',
            'policy': {'weights': weights, 'grade_points': config['grade_points'], 'high_risk_cap': config['high_risk_cap'],
                       'coverage': config['coverage'], 'grades': config['grades']}}
