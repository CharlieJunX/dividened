"""Calculations expose their inputs and never interpret absent amounts as zero."""
from datetime import date, timedelta
import math
from .periods import versions, quarters, annuals, ttm_pair, ttm_window, growth

LABELS = {
    'revenue_growth': '매출 성장률', 'operating_income_growth': '영업이익 성장률',
    'net_income_growth': '순이익 성장률', 'eps_growth': '희석 EPS 성장률',
    'ocf': '영업현금흐름', 'ocf_income': '영업현금흐름/순이익', 'fcf': 'FCF',
    'net_debt': '순차입금', 'net_debt_ebitda': '순차입금/EBITDA',
    'interest_coverage': '이자보상배율', 'dividend_fcf': '현금배당/FCF',
    'dividend_income': '현금배당/순이익', 'dividend_growth': '배당 증가율',
    'dividend_profit_growth': '배당 비교용 연간 순이익 성장률',
    'dividend_cut': '최근 5년 배당 삭감', 'roe': 'ROE', 'roe_change': 'ROE 변화(%p)',
    'cet1': 'CET1(%)', 'npl': 'NPL(%)', 'solvency': '지급여력(%)',
    'credit_loss_growth': '신용손실 증가율', 'ffo_growth': 'FFO 성장률',
    'affo_growth': 'AFFO 성장률', 'rental_income_growth': '임대수익 성장률',
    'dividend_affo': '현금배당/AFFO', 'insurance_income_growth': '보험영업손익 성장률',
    'investment_income_growth': '투자손익 성장률', 'revenue_trend': '최근 3년 매출 추세',
}


def calculate(raw, as_of, industry, config, review=None):
    review = review or {}
    facts = versions(raw, as_of)
    grouped = {}
    for f in facts:
        grouped.setdefault(f.metric, []).append(f)
    issues = []
    # Mixing currencies or accounting scopes would manufacture apparent growth.
    bases = {f.basis for f in facts if f.metric in {'revenue', 'net_income'}}
    if len(bases) > 1:
        issues.append('통화·연결범위·회계기준 불일치')
        grouped = {}
    elif bases:
        monetary_basis = next(iter(bases))
        for metric, values in list(grouped.items()):
            # Per-share and percentage units differ; currency, scope and standard must agree.
            grouped[metric] = [f for f in values if f.scope == monetary_basis[1]
                               and f.standard == monetary_basis[2]
                               and (f.unit == 'percent' or f.unit.split('/')[0] == monetary_basis[0])]
    metrics = {}

    def put(key, value=None, evidence=(), reason='', state='NORMAL'):
        metrics[key] = {'value': value, 'state': state if value is not None else 'N/A',
                        'reason': reason, 'evidence': [f.dump() for f in evidence]}

    def series(metric):
        return annuals(grouped.get(metric, []))

    anchor_metric = 'net_income' if industry in {'BANK', 'INSURER', 'OTHER_FINANCIAL'} else 'revenue'
    anchor_qs = quarters(grouped.get(anchor_metric, []))
    anchor = anchor_qs[-1].end if anchor_qs else None
    anchor_annual = series(anchor_metric)
    annual_end = anchor_annual[-1].end if anchor_annual else None

    def annual(metric, end=annual_end):
        matching = [f for f in series(metric) if f.end == end]
        return matching[-1] if len(matching) == 1 else None

    def instant(metric, end):
        matching = [f for f in grouped.get(metric, []) if f.start is None and f.end == end]
        return matching[0] if len(matching) == 1 else None

    def compare(metric):
        key = metric + '_growth'
        pair = ttm_pair(grouped.get(metric, []), anchor) if anchor else None
        if not pair:
            put(key, reason='비교 가능한 8개 분기 또는 EPS 기준 미확인')
            return
        current, prior, evidence = pair
        value = growth(current, prior)
        state, reason = 'NORMAL', ''
        if prior > 0 and current < 0:
            state, reason = 'SEVERE', '흑자에서 적자 전환'
        elif prior <= 0:
            # A status without a percent is a valid evaluated result.
            state = 'WARNING' if current <= 0 else 'NORMAL'
            reason = '적자 지속 또는 0 이하' if current <= 0 else '흑자 전환'
        if key not in config['industries'][industry]['thresholds']:
            state = 'NORMAL'  # Descriptive series is not an extra unconfigured risk rule.
        put(key, value, evidence, reason, state)
        if prior <= 0:
            metrics[key]['state'] = state
            metrics[key]['evaluated'] = True
        metrics[key].update(current=current, prior=prior)

    common = [] if industry == 'REIT' else ['net_income', 'eps']
    if industry == 'GENERAL':
        common += ['revenue', 'operating_income']
    elif industry == 'REIT':
        common += ['affo', 'ffo', 'rental_income']
    elif industry == 'BANK':
        common += ['credit_loss']
    elif industry == 'INSURER':
        common += ['insurance_income', 'investment_income']
    for metric in common:
        compare(metric)

    def ratio(key, a, b, positive=True):
        valid = a is not None and b is not None and a.start == b.start and a.end == b.end and a.basis == b.basis
        if not valid or (positive and b.value <= 0) or (b is not None and b.value == 0):
            put(key, reason='동일 기간·단위의 분자/양수 분모 확인 필요')
        else:
            put(key, a.value / b.value, [a, b])

    if industry == 'GENERAL':
        rev = series('revenue')[-3:]
        valid_rev = len(rev) == 3 and all(330 <= (date.fromisoformat(b.end) - date.fromisoformat(a.end)).days <= 380 for a, b in zip(rev, rev[1:]))
        put('revenue_trend', [f.value for f in rev] if valid_rev else None, rev, '최근 3개 회계연도 매출')
        ocfs = series('ocf')[-3:]
        valid_ocfs = (len(ocfs) == 3 and ocfs[-1].end == annual_end and
                      all(330 <= (date.fromisoformat(b.end) - date.fromisoformat(a.end)).days <= 380 and a.basis == b.basis
                          for a, b in zip(ocfs, ocfs[1:])))
        if valid_ocfs:
            state = 'SEVERE' if all(f.value < 0 for f in ocfs[-2:]) else 'WARNING' if ocfs[-1].value < 0 else 'NORMAL'
            put('ocf', ocfs[-1].value, ocfs, '2년 연속 음수' if state == 'SEVERE' else '최근 연간 현금흐름', state)
        else:
            put('ocf', reason='동일 기준 3개 연간 OCF 부족')
        ratio('ocf_income', annual('ocf'), annual('net_income'))
        fcfs, evidence = [], []
        for ocf in ocfs:
            capex = annual('capex', ocf.end)
            if capex and capex.value >= 0 and ocf.start == capex.start and ocf.basis == capex.basis:
                fcfs.append(ocf.value - capex.value)
                evidence.extend([ocf, capex])
        if valid_ocfs and len(fcfs) == 3:
            state = 'SEVERE' if all(f < 0 for f in fcfs[-2:]) else 'WARNING' if fcfs[-1] < 0 else 'NORMAL'
            capex_series = evidence[1::2]
            reason = '2년 연속 음수' if state == 'SEVERE' else '최근 연간 FCF'
            if capex_series[-1].value > capex_series[-2].value:
                reason += '; CAPEX 증가, 투자 확대 가능성 확인 필요'
            put('fcf', fcfs[-1], evidence, reason, state)
        else:
            put('fcf', reason='3개 연간 OCF·양수 지출 기준 CAPEX 부족')
        dividends = annual('dividends')
        fcf = metrics['fcf']['value']
        if dividends and dividends.value >= 0 and fcf is not None and dividends.basis == evidence[-1].basis and dividends.start == evidence[-1].start:
            # Negative/zero FCF makes payout multiple meaningless; excess cash use still warns.
            state = 'WARNING' if dividends.value > fcf else 'NORMAL'
            put('dividend_fcf', dividends.value / fcf if fcf > 0 else None,
                [dividends] + evidence[-2:], '배당이 FCF 초과' if state == 'WARNING' else '', state)
            metrics['dividend_fcf'].update(state=state, evaluated=True)
        else:
            put('dividend_fcf', reason='현금배당 지급액 또는 FCF 미확인')

    if industry in {'GENERAL', 'REIT'}:
        balance_end = anchor
        cash, debt = instant('cash', balance_end), instant('total_debt', balance_end)
        components = [instant(k, balance_end) for k in ('short_debt', 'long_debt', 'current_long_debt')]
        debt_evidence = [debt] if debt else components
        # Summing three tags does not prove bonds/leases/other interest-bearing debt are covered.
        complete = debt is not None or (review.get('debt_components_complete') is True
                                        and review.get('source') and review.get('reviewed_on', '9999') <= str(as_of)
                                        and all(components))
        if complete and cash and cash.value >= 0 and all(f and f.value >= 0 and f.basis == cash.basis for f in debt_evidence):
            net = sum(f.value for f in debt_evidence) - cash.value
            put('net_debt', net, debt_evidence + [cash])
        else:
            net = None
            put('net_debt', reason='전체 이자부 차입금 범위 미확인')
        ebitda = ttm_window(grouped.get('ebitda', []), anchor) if anchor else None
        if net is not None and ebitda and ebitda[0] > 0 and cash.basis == ebitda[1][-1].basis:
            put('net_debt_ebitda', net / ebitda[0], debt_evidence + [cash] + ebitda[1])
        else:
            put('net_debt_ebitda', reason='EBITDA 0 이하' if ebitda and ebitda[0] <= 0 else '차입금 또는 TTM EBITDA 부족')
        op = ttm_window(grouped.get('operating_income', []), anchor) if anchor else None
        interest = ttm_window(grouped.get('interest', []), anchor) if anchor else None
        if op and interest and interest[0] > 0 and op[1][-1].basis == interest[1][-1].basis:
            put('interest_coverage', op[0] / interest[0], op[1] + interest[1])
        else:
            put('interest_coverage', reason='영업이익 또는 이자비용 부족')

    div, income = annual('dividends'), annual('net_income')
    if div and div.value < 0:
        div = None
    ratio('dividend_income', div, income)
    if industry == 'REIT':
        ratio('dividend_affo', div, annual('affo'))

    dps = series('dps')[-6:]
    # Per-share series require the same split adjustment review as EPS.
    if (len(dps) == 6 and review.get('dividend_split_verified') is True and review.get('eps_source')
            and review.get('reviewed_on', '9999') <= str(as_of)
            and all(330 <= (date.fromisoformat(b.end) - date.fromisoformat(a.end)).days <= 380 and a.basis == b.basis for a, b in zip(dps, dps[1:]))):
        put('dividend_cut', any(b.value < a.value for a, b in zip(dps, dps[1:])), dps)
        put('dividend_growth', growth(dps[-1].value, dps[-2].value), dps[-2:])
    else:
        put('dividend_cut', reason='6개 연간 DPS 또는 분할 조정 근거 부족')
        put('dividend_growth', reason='DPS 성장률 비교 불가')

    if industry in {'BANK', 'INSURER', 'OTHER_FINANCIAL'}:
        roes, roe_evidence, roe_ends = [], [], []
        for inc in series('net_income')[-2:]:
            opening = (date.fromisoformat(inc.start) - timedelta(days=1)).isoformat()
            before, after = instant('equity', opening), instant('equity', inc.end)
            if before and after and before.value > 0 and after.value > 0 and before.basis == after.basis == inc.basis:
                roes.append(inc.value / ((before.value + after.value) / 2) * 100)
                roe_evidence.extend([inc, before, after])
                roe_ends.append(inc.end)
        latest_roe = bool(roe_ends and roe_ends[-1] == annual_end)
        comparable_roe = latest_roe and len(roe_ends) == 2 and 330 <= (date.fromisoformat(roe_ends[1]) - date.fromisoformat(roe_ends[0])).days <= 380
        put('roe', roes[-1] if latest_roe else None, roe_evidence)
        put('roe_change', roes[-1] - roes[-2] if comparable_roe else None, roe_evidence)
        capital_metrics = ('cet1', 'npl') if industry == 'BANK' else ('solvency',) if industry == 'INSURER' else ()
        for key in capital_metrics:
            f = instant(key, anchor)
            put(key, f.value if f and f.unit == 'percent' else None, [f] if f else [], '공시 비율 또는 정의 확인 필요')
            if key == 'solvency' and key not in config['industries'][industry]['thresholds']:
                metrics[key].update(state='N/A', reason='국가별 지급여력 정의·임계값 설정 필요')

    for key in config['industries'][industry]['required']:
        if key not in metrics:
            put(key, reason='지원 자료 없음')
    for key, bounds in config['industries'][industry]['thresholds'].items():
        m = metrics.get(key)
        if not m or m['value'] is None or m['state'] == 'SEVERE':
            continue
        low = key.endswith('_growth') and key != 'credit_loss_growth' or key in {'roe_change', 'cet1', 'solvency', 'interest_coverage'}
        value = m['value']
        if low:
            at_most = lambda limit: value <= limit or math.isclose(value, limit, rel_tol=1e-10, abs_tol=1e-10)
            state = 'SEVERE' if at_most(bounds[1]) else 'WARNING' if at_most(bounds[0]) else 'NORMAL'
        else:
            state = 'SEVERE' if value > bounds[1] else 'WARNING' if value > bounds[0] else 'NORMAL'
        if m['state'] == 'NORMAL':
            m['state'] = state
        if state != 'NORMAL' and not m['reason']:
            m['reason'] = f'{LABELS.get(key, key)} 기준 초과'
    if metrics['dividend_cut']['value'] is True:
        metrics['dividend_cut'].update(state='WARNING', reason='최근 5년 배당 삭감')
    # Compare dividend and profit growth over exactly the same annual windows, not annual vs TTM.
    profit = None
    ni = series('net_income')[-2:]
    if len(dps) >= 2 and len(ni) == 2 and all(a.start == b.start and a.end == b.end
                                             for a, b in zip(dps[-2:], ni)):
        profit = growth(ni[-1].value, ni[-2].value)
    put('dividend_profit_growth', profit, ni, '주당배당금과 동일 회계연도의 순이익 성장률')
    div_growth = metrics['dividend_growth']['value']
    if div_growth is not None and profit is not None and div_growth > profit:
        metrics['dividend_growth'].update(state='WARNING', reason='배당 증가율이 이익 증가율 초과')

    coverage = {'annual_periods': len(anchor_annual), 'quarter_periods': len(anchor_qs),
                'latest_annual_end': annual_end, 'latest_quarter_end': anchor}
    if len(anchor_annual) < config['min_annual_periods']:
        issues.append('연간 이력 5개 미만')
    if any(not 330 <= (date.fromisoformat(b.end) - date.fromisoformat(a.end)).days <= 380
           for a, b in zip(anchor_annual[-config['min_annual_periods']:], anchor_annual[-config['min_annual_periods'] + 1:])):
        issues.append('연간 회계기간 불연속')
    if not ttm_pair(grouped.get(anchor_metric, []), anchor) or len(anchor_qs) < config['min_quarter_periods']:
        issues.append('연속된 비교 가능 분기 이력 부족')
    if not annual_end or (as_of - date.fromisoformat(annual_end)).days > config['max_annual_age_days']:
        issues.append('최신 연간 자료 지연 또는 누락')
    if not anchor or (as_of - date.fromisoformat(anchor)).days > config['max_quarter_age_days']:
        issues.append('최신 분기 자료 지연 또는 누락')
    return metrics, coverage, issues
