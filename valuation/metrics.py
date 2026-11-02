"""No negative multiples, fabricated capital adjustments, or current EPS in old prices."""
from dataclasses import replace
from datetime import date
import math
from dividend_metrics import currency_unit
from fundamentals.periods import versions, annuals, ttm_window, consecutive
from .models import Observation


def verified(review, key):
    return review.get(key) is True and bool(review.get('source') and review.get('reviewed_on'))


def split_ratio(history, start, end):
    splits = history.get('splits', [])
    if len({s['date'] for s in splits}) != len(splits):
        raise ValueError('Duplicate stock splits')
    values = [s['ratio'] for s in splits if start < s['date'] <= end]
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError('Invalid split ratio')
    return math.prod(values)


def price_at(history, day, config):
    rows = [p for p in history.get('prices', []) if p['date'] <= str(day) and p.get('close') is not None]
    if not rows:
        return Observation(reason='해당 시점 가격 없음')
    latest = max(rows, key=lambda p: p['date'])
    if sum(p['date'] == latest['date'] for p in rows) != 1:
        return Observation(reason='평가일 가격 중복')
    price = latest['close']
    if type(price) not in (int, float) or not math.isfinite(price) or price <= 0:
        return Observation(reason='유효하지 않은 주가')
    if (day - date.fromisoformat(latest['date'])).days > config['max_price_age_days']:
        return Observation(reason='가격 지연')
    basis = history.get('basis')
    if basis == 'split_adjusted':
        # Undo splits after the valuation date to reconstruct that date's raw quote.
        price *= split_ratio(history, str(day), history.get('as_of', str(day)))
    elif basis != 'raw':
        return Observation(reason='가격 분할 조정 기준 미확인')
    currency, scale = currency_unit(history.get('currency'))
    if not currency:
        return Observation(reason='주가 통화 미확인')
    return Observation(price * scale, evidence=[{'date': latest['date'], 'source': history.get('source'),
                                               'currency': currency, 'basis': 'raw_at_valuation_date'}], period_end=latest['date'])


class Context:
    def __init__(self, facts, day, currency, review, config, history):
        self.day, self.currency, self.review = day, currency, review
        self.config, self.history = config, history
        self.facts = versions(facts, day)

    def group(self, metric):
        return [f for f in self.facts if f.metric == metric and
                (f.scope == self.review.get('scope', 'consolidated') or (metric == 'common_shares' and f.scope == 'issuer'))]

    def balance(self, metric, end=None):
        fs = [f for f in self.group(metric) if f.start is None and (end is None or f.end == end)]
        if not fs:
            return None
        latest = max(f.end for f in fs)
        fs = [f for f in fs if f.end == latest]
        if len(fs) != 1 or (self.day - date.fromisoformat(latest)).days > self.config['max_financial_age_days']:
            return None
        f = fs[0]
        if f.unit not in {self.currency, 'shares', 'percent'}:
            return None
        return f

    def flow(self, metric):
        fs = self.group(metric)
        if metric == 'eps':
            converted = []
            for f in fs:
                if f.unit != self.currency + '/shares' or not f.eps_verified:
                    continue
                if split_ratio(self.history, f.start or f.end, str(self.day)) != 1:
                    if not verified(self.review, 'eps_split_basis_verified') or self.review.get('eps_basis') != 'period_end':
                        continue
                    f = replace(f, value=f.value / split_ratio(self.history, f.end, str(self.day)))
                converted.append(f)
            fs = converted
        else:
            fs = [f for f in fs if f.unit == self.currency]
        window = ttm_window(fs)
        ann = annuals(fs)
        # A reported fiscal year is a twelve-month window, but never replace newer interim results with it.
        if ann:
            latest = max(ann, key=lambda f: f.end)
            if (not window or latest.end > window[1][-1].end) and len({f.basis for f in ann if f.end == latest.end}) == 1:
                window = latest.value, [latest]
        if not window:
            return None
        if window[1][-1].end != max(f.end for f in fs):
            return None
        if (self.day - date.fromisoformat(window[1][-1].end)).days > self.config['max_financial_age_days']:
            return None
        return window


def calculate_point(facts, history, day, industry, config, review=None):
    review = review or {}
    price = price_at(history, day, config['valuation'])
    currency = currency_unit(history.get('currency'))[0]
    ctx = Context(facts, day, currency, review, config['valuation'], history)
    policy = config['industries'][industry]['valuation']
    results = {metric: Observation(reason='필수 재무 자료 또는 검증 근거 부족').dump() for metric in policy}
    warnings = []
    if price.value is None:
        return {'date': str(day), 'price': price.dump(), 'metrics': results, 'warnings': [price.reason]}

    def put(key, value, evidence, end, reason=''):
        if key not in results:
            return
        if key != 'fcf_yield' and value <= 0:
            results[key] = Observation(reason='0 이하 배수', evidence=evidence, period_end=end).dump()
        elif key != 'fcf_yield' and value > config['valuation']['max_multiples'].get(key, 10000):
            results[key] = Observation(value, '설정된 배수 상한 초과: 계산값 보존, 저평가·점수 평가 제외', evidence, end).dump()
            results[key]['eligible'] = False
        else:
            results[key] = Observation(value, reason, evidence, end).dump()

    eps = ctx.flow('eps')
    if 'per' in results and eps:
        if eps[0] > 0:
            put('per', price.value / eps[0], [f.dump() for f in eps[1]] + price.evidence, eps[1][-1].end)
        else:
            results['per'] = Observation(reason='TTM 희석 EPS 0 이하', evidence=[f.dump() for f in eps[1]]).dump()
    market_cap = ctx.balance('market_cap', str(day))
    cap, cap_evidence = None, []
    if market_cap and market_cap.value > 0:
        cap, cap_evidence = market_cap.value, [market_cap.dump()]
    else:
        shares = ctx.balance('common_shares')
        share_basis_verified = verified(review, 'common_shares_verified') or bool(shares and shares.scope == 'issuer'
            and shares.standard == 'corporate-share-register' and shares.tag == 'stockTotqySttus.distb_stock_co')
        if (shares and shares.unit == 'shares' and shares.value > 0 and share_basis_verified
                and (day - date.fromisoformat(shares.end)).days <= config['valuation']['max_shares_age_days']):
            count = shares.value * split_ratio(history, shares.end, str(day))
            cap, cap_evidence = count * price.value, [shares.dump()] + price.evidence
            warnings.append('시가총액은 해당 시점에 알려진 최근 공시 보통주 수 기준; 중간 자사주 변동 확인 필요')
    common = ctx.balance('common_equity')
    if common is None and verified(review, 'common_equity_verified'):
        parent = ctx.balance('equity_parent')
        preferred = ctx.balance('preferred_equity', parent.end) if parent else None
        if parent and preferred and parent.basis == preferred.basis and preferred.value >= 0:
            common = replace(parent, metric='common_equity', value=parent.value - preferred.value,
                             source=parent.source + '; ' + preferred.source,
                             accession=parent.accession + ' minus ' + preferred.accession)
    if 'pbr' in results and cap is not None and common and common.value > 0 and verified(review, 'common_equity_verified'):
        put('pbr', cap / common.value, cap_evidence + [common.dump()], common.end)
    elif 'pbr' in results and common and common.value <= 0:
        results['pbr'] = Observation(reason='보통주 귀속 자기자본 0 이하', evidence=[common.dump()]).dump()
    if 'fcf_yield' in results and cap is not None:
        ocf, capex = ctx.flow('ocf'), ctx.flow('capex')
        if (ocf and capex and ocf[1][0].start == capex[1][0].start
                and ocf[1][-1].end == capex[1][-1].end and ocf[1][-1].basis == capex[1][-1].basis
                and capex[0] >= 0 and all(f.value >= 0 for f in capex[1])):
            fcf = ocf[0] - capex[0]
            put('fcf_yield', fcf / cap * 100, cap_evidence + [f.dump() for f in ocf[1] + capex[1]],
                ocf[1][-1].end, '음수 FCF: 저평가 신호·점수 산정 제외' if fcf <= 0 else '')
    if 'ev_ebitda' in results and cap is not None:
        debt = ctx.balance('total_debt')
        cash = ctx.balance('cash', debt.end) if debt else None
        adjustment = ctx.balance('ev_adjustments', debt.end) if debt else None
        ebitda = ctx.flow('ebitda')
        if (debt and cash and adjustment and ebitda and ebitda[0] > 0 and debt.value >= 0 and cash.value >= 0
                and debt.basis == cash.basis == adjustment.basis == ebitda[1][-1].basis
                and debt.end == ebitda[1][-1].end and verified(review, 'ev_adjustments_verified') and review.get('ev_definition')):
            ev = cap + debt.value - cash.value + adjustment.value
            put('ev_ebitda', ev / ebitda[0], cap_evidence + [f.dump() for f in [debt, cash, adjustment] + ebitda[1]], debt.end)
        elif ebitda and ebitda[0] <= 0:
            results['ev_ebitda'] = Observation(reason='TTM EBITDA 0 이하', evidence=[f.dump() for f in ebitda[1]]).dump()
    for metric, name in [('p_affo', 'affo'), ('p_ffo', 'ffo')]:
        if metric in results and cap is not None and verified(review, 'common_earnings_verified') and review.get('ffo_definition'):
            flow = ctx.flow(name)
            if flow and flow[0] > 0:
                put(metric, cap / flow[0], cap_evidence + [f.dump() for f in flow[1]], flow[1][-1].end)
    return {'date': str(day), 'price': price.dump(), 'market_cap': cap, 'metrics': results, 'warnings': warnings,
            'ev_definition': review.get('ev_definition'), 'currency': currency}
