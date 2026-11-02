from datetime import date, timedelta
from dataclasses import replace
from fundamentals.periods import annuals, versions
from valuation.metrics import split_ratio, verified


def calculate(facts, history, as_of, industry, config, review=None):
    review = review or {}
    fs = versions(facts, as_of)
    grouped = {}
    currency = history.get('currency')
    from dividend_metrics import currency_unit
    currency = currency_unit(currency)[0]
    for f in fs:
        if f.scope == review.get('scope', 'consolidated'):
            grouped.setdefault(f.metric, []).append(f)

    def series(metric):
        return annuals([f for f in grouped.get(metric, []) if f.unit in {currency, currency + '/shares' if currency else '', 'percent'}])

    def pairs(a, b):
        result = []
        for f in a:
            matches = [g for g in b if g.start == f.start and g.end == f.end and g.basis == f.basis]
            if len(matches) == 1 and matches[0].value >= 0:
                g = matches[0]
                result.append(replace(f, metric='fcf', value=f.value - g.value,
                                      filed=max(f.filed, g.filed), source=f.source + '; ' + g.source,
                                      accession=f.accession + ' minus ' + g.accession))
        return result

    output = {}
    for metric, weight in config['industries'][industry]['growth'].items():
        values = series(metric)
        if metric == 'fcf':
            values = pairs(series('ocf'), series('capex'))
        if metric == 'roe' and not values:
            for inc in series('net_income'):
                opening = (date.fromisoformat(inc.start) - timedelta(days=1)).isoformat()
                before = [f for f in grouped.get('equity', []) if f.start is None and f.end == opening and f.basis == inc.basis and f.value > 0]
                after = [f for f in grouped.get('equity', []) if f.start is None and f.end == inc.end and f.basis == inc.basis and f.value > 0]
                if len(before) == len(after) == 1:
                    values.append(replace(inc, metric='roe', value=inc.value / ((before[0].value + after[0].value) / 2) * 100,
                                          unit='percent', source=inc.source + '; ' + before[0].source + '; ' + after[0].source))
        values = sorted(values, key=lambda f: f.end)[-4:]
        reason = ''
        if metric == 'eps':
            if any(not f.eps_verified for f in values):
                reason = '연간 희석 EPS 합산·분할 기준 미검증'
            elif values and split_ratio(history, values[0].start, str(as_of)) != 1:
                if verified(review, 'eps_split_basis_verified') and review.get('eps_basis') == 'period_end':
                    values = [replace(f, value=f.value / split_ratio(history, f.end, str(as_of))) for f in values]
                else:
                    reason = '성장 비교 구간의 분할 조정 미검증'
        if metric in {'affo', 'ffo'} and not (verified(review, 'common_earnings_verified') and review.get('ffo_definition')):
            reason = 'FFO/AFFO 정의·보통주 귀속 기준 미검증'
        valid = (len(values) == 4 and len({f.basis for f in values}) == 1
                 and all(330 <= (date.fromisoformat(b.end) - date.fromisoformat(a.end)).days <= 380
                         for a, b in zip(values, values[1:]))
                 and (as_of - date.fromisoformat(values[-1].end)).days <= 550)
        if not valid:
            reason = '동일 기준 연속 4개 연간 실적 부족 또는 자료 지연'
        if values and any(f.value <= 0 for f in values):
            reason = '0 이하 실적/적자 전환: 일반 CAGR 평가 불가'
        score, cagr, consistency = None, None, None
        if valid and not reason:
            cagr = ((values[-1].value / values[0].value) ** (1 / 3) - 1) * 100
            changes = [(b.value / a.value - 1) * 100 for a, b in zip(values, values[1:])]
            consistency = sum(v > 0 for v in changes) / 3
            fraction = max(0, min(1, cagr / config['growth']['full_score_cagr_pct'])) * consistency
            if max(changes) > config['growth']['one_off_jump_pct']:
                fraction = min(fraction, config['growth']['one_off_max_fraction'])
                reason = '일회성 급증 가능성: 성장 점수 상한 적용'
            score = weight * fraction
        output[metric] = {'score': score, 'weight': weight, 'cagr_pct': cagr, 'consistency': consistency,
                          'reason': reason, 'evidence': [f.dump() for f in values]}
    return output
