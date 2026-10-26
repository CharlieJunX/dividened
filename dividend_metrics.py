"""Pure dividend calculations. All percentages are percentage points, not fractions."""
import math
from datetime import date, timedelta
from statistics import mean, median

from dividend_data import shift_months


def number(value):
    if isinstance(value, bool):
        raise ValueError('Boolean numeric value')
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Non-positive or non-finite value')
    return value


def currency_unit(currency):
    # Yahoo London quotes may be pence; no unverified FX conversion.
    return {'GBp': ('GBP', .01), 'GBX': ('GBP', .01), 'ZAc': ('ZAR', .01)}.get(currency, (currency, 1))


def normalise(history, review, include_all=False):
    """Validate, classify only with evidence, and align price/dividend share basis.

    Non-Yahoo adapters may provide raw, contemporaneous per-share data using
    basis='raw'; both prices and dividends are then divided by later splits.
    """
    issues, prices, dividends, splits = list(history.get('dividend_issues', [])), [], [], []
    as_of = date.fromisoformat(history['as_of'])
    for event in history['splits']:
        splits.append((date.fromisoformat(event['date']), number(event['ratio'])))
    if len({d for d, _ in splits}) != len(splits):
        issues.append('중복 주식분할')
    basis = history.get('basis')
    if basis not in {'raw', 'split_adjusted'}:
        issues.append('주당 기준 확인 필요')
    def factor(day):
        return math.prod(r for d, r in splits if day < d <= as_of) if basis == 'raw' else 1
    price_currency, price_scale = currency_unit(history.get('currency'))
    if not price_currency:
        issues.append('주가 통화 누락')
    seen = set()
    for item in history['prices']:
        day = date.fromisoformat(item['date'])
        # No bar on a holiday (or an isolated missing bar) is not a corrupt
        # price. Keep it out of the series; gap/staleness checks run below.
        if day > as_of or item['close'] is None:
            continue
        try:
            value = number(item['close']) * price_scale / factor(day)
        except (ValueError, TypeError):
            issues.append('주가 누락/비정상 값')
            continue
        if day in seen:
            issues.append('중복 주가')
            continue
        seen.add(day)
        if day <= as_of:
            prices.append((day, value))
    seen = set()
    for item in history['dividends']:
        day = date.fromisoformat(item['date'])
        amount = number(item['amount'])
        if day in seen:
            issues.append('중복 배당락일')
            continue
        seen.add(day)
        if day > as_of:
            issues.append('미래 배당락일')
            continue
        verified = bool(review.get('date_basis', 'ex_date') == history.get('dividend_date_basis', 'ex_date')
                        and review.get('source') and review.get('from') and review.get('through')
                        and date.fromisoformat(review['from']) <= day <= date.fromisoformat(review['through']))
        override = review.get('events', {}).get(day.isoformat(), {}) if verified else {}
        kind = override.get('kind', review.get('default_kind', item.get('kind', 'unknown'))) if verified else item.get('kind', 'unknown')
        # An explicitly typed upstream source is supported by other adapters.
        div_currency = override.get('currency', review.get('dividend_currency', item.get('currency')))
        div_currency, div_scale = currency_unit(div_currency)
        if not div_currency or div_currency != price_currency:
            issues.append('배당 통화 확인 필요/불일치')
        if kind not in {'regular', 'special', 'mixed'}:
            if not include_all:
                issues.append('정기/특별배당 확인 필요')
            kind = 'unknown'
        regular = amount if kind == 'regular' else 0
        if kind == 'mixed':
            regular = number(override['regular_amount'])
            special = number(override['special_amount'])
            if not math.isclose(regular + special, amount, rel_tol=1e-6):
                raise ValueError('Regular/special components do not match provider amount')
        included = amount if include_all else regular
        dividends.append((day, included * div_scale / factor(day), kind))
    return sorted(prices), sorted(dividends), sorted(set(issues))


def ttm(dividends, day, include_all=False):
    start = shift_months(day, -12)
    return sum(value for d, value, kind in dividends
               if start < d <= day and (include_all or kind in {'regular', 'mixed'}))


def month_end_yields(prices, dividends, as_of, months, quality, include_all=False):
    """Completed calendar months; TTM is evaluated at calendar month end.

    Use the last trading close on/before that date, never a future close.
    Every month must have its entire preceding 12 months covered.
    """
    last = as_of.replace(day=1) - timedelta(days=1)
    yields = []
    for offset in reversed(range(months)):
        first = shift_months(last.replace(day=1), -offset)
        end = shift_months(first, 1) - timedelta(days=1)
        if not prices or prices[0][0] > shift_months(end, -12):
            return None, []
        available = [(d, p) for d, p in prices if d <= end]
        if not available or (end - available[-1][0]).days > quality['max_price_gap_days']:
            return None, []
        value = ttm(dividends, end, include_all) / available[-1][1] * 100
        if value > quality['max_yield_pct']:
            return None, []
        yields.append({'date': end.isoformat(), 'price_date': available[-1][0].isoformat(),
                       'yield_pct': value})
    return mean(item['yield_pct'] for item in yields), yields


def fiscal_year_metrics(history, years, include_all):
    """Annual Korean totals come from DART, independent of event/price coverage."""
    source = history.get('dart_annual', {})
    annual, warnings = {}, []
    for year in years:
        info = source.get(str(year), source.get(year, {}))
        value = info.get('amount')
        try:
            if value is None or isinstance(value, bool):
                raise ValueError
            value = float(value)
            if not math.isfinite(value) or value < 0:
                raise ValueError
        except (ValueError, TypeError):
            value = None
        annual[year] = value
    if not include_all:
        warnings.append('DART 연간 총배당금은 정기·특별 분리 불가: 정기배당 성장률·연간 삭감 N/A')
        return {year: None for year in years}, None, None, warnings
    if any(value is None for value in annual.values()):
        warnings.append('DART 사업연도 배당 이력 부족: 5년 성장률·연간 삭감 N/A')
        return annual, None, None, warnings
    if any(date(years[0], 1, 1) <= date.fromisoformat(s['date']) <= date.fromisoformat(history['as_of'])
           for s in history['splits']):
        warnings.append('DART 연간 주당배당금 분할·병합 기준 확인 필요: 성장률·연간 삭감 N/A')
        return annual, None, None, warnings
    cagr = ((annual[years[-1]] / annual[years[0]]) ** .2 - 1) * 100 if all(annual.values()) else None
    cut = any(annual[b] < annual[a] - 1e-8 for a, b in zip(years, years[1:]))
    return annual, cagr, cut, warnings


def calculate(history, current, quote_currency, review, quality, include_all=False):
    prices, dividends, issues = normalise(history, review, include_all)
    as_of = date.fromisoformat(history['as_of'])
    current = number(current)
    qc, scale = currency_unit(quote_currency)
    hc, _ = currency_unit(history.get('currency'))
    current *= scale
    if not qc or qc != hc:
        issues.append('현재가/이력 통화 불일치')
    if not prices:
        raise ValueError('No usable prices')
    if (as_of - prices[-1][0]).days > quality['max_price_age_days']:
        issues.append('최근 주가 지연')
    if abs(current / prices[-1][1] - 1) * 100 > quality['quote_tolerance_pct']:
        issues.append('현재가/이력 가격 불일치')
    if any((b[0] - a[0]).days > quality['max_price_gap_days'] for a, b in zip(prices, prices[1:])):
        issues.append('주가 이력 공백')
    # Price jumps flag corrupt split bases, unit changes, and other outliers.
    if any(max(b[1] / a[1], a[1] / b[1]) > 4 for a, b in zip(prices, prices[1:])):
        issues.append('주가 급변/분할 기준 확인 필요')
    missing_price_dates = sorted({item['date'] for item in history['prices']
                                  if item['close'] is None and date.fromisoformat(item['date']) <= as_of})
    warnings = [f'종가 없는 날짜 {len(missing_price_dates)}개 제외: 직전 유효 종가 사용'] if missing_price_dates else []
    warnings.extend(history.get('dividend_warnings', []))
    if include_all:
        warnings.append('정기·특별배당 모두 포함: 일회성 배당으로 수익률·성장률·삭감 판정이 왜곡될 수 있음')
        if any(k == 'unknown' for _, _, k in dividends):
            warnings.append('종류 미확인 배당도 총배당금에 포함')
    paid = [(d, a) for d, a, k in dividends if include_all or k in {'regular', 'mixed'}]
    total = ttm(dividends, as_of, include_all)
    domestic = history.get('dividend_date_basis') == 'record_date'
    if not paid or total == 0:
        if domestic:
            warnings.append('최근 12개월 국내 배당 미확인: 무배당 미확정')
        elif any(k == 'unknown' and shift_months(as_of, -12) < d <= as_of for d, _, k in dividends):
            warnings.append('최근 12개월 배당 분류 확인 필요')
        else:
            warnings.append('최근 12개월 배당 없음' if include_all else '최근 12개월 정기배당 없음')
        issues.append('TTM 국내 배당 미확인' if domestic else
                      'TTM 배당 없음' if include_all else 'TTM 정기배당 없음/확인 필요')
    if len(paid) >= 3:
        gaps = [(b[0] - a[0]).days for a, b in zip(paid, paid[1:])]
        expected = median(gaps[-8:])
        if (as_of - paid[-1][0]).days > expected + quality['dividend_delay_grace_days']:
            warnings.append('배당기준일 지연/이력 누락 가능성' if domestic else '배당 지급 지연/중단 가능성')
            issues.append('배당 일정 확인 필요')
        for index, gap in enumerate(gaps):
            # Do not retroactively judge annual payments using a later
            # semiannual/quarterly cadence. Require two prior intervals.
            previous = gaps[max(0, index - 8):index]
            if len(previous) < 2:
                continue
            historical_expected = median(previous)
            limit = historical_expected * 1.5 + min(quality['dividend_delay_grace_days'], historical_expected * .25)
            if gap > limit:
                issues.append('배당 이력 누락/중단 확인 필요')
                break
    avg3, monthly3 = month_end_yields(prices, dividends, as_of, 36, quality, include_all)
    avg5, monthly5 = month_end_yields(prices, dividends, as_of, 60, quality, include_all)
    if avg3 is None:
        issues.append('3년 평균 이력 부족')
    if avg5 is None:
        warnings.append('5년 평균 이력 부족')
    # Six completed calendar years give an actual five-year endpoint interval.
    years = list(range(as_of.year - 6, as_of.year))
    record_annual = {year: sum(a for d, a in paid if d.year == year) for year in years}
    if domestic:
        annual, cagr, cut, annual_warnings = fiscal_year_metrics(history, years, include_all)
        warnings.extend(annual_warnings)
    else:
        annual = record_annual
        complete = prices[0][0] <= date(years[0], 1, 1)
        cagr = ((annual[years[-1]] / annual[years[0]]) ** (1 / 5) - 1) * 100 if complete and all(annual.values()) else None
        cut = None
        if complete:
            cut = any(annual[b] < annual[a] - 1e-8 for a, b in zip(years, years[1:]))
            # Foreign calculations retain the existing TTM cut check.
            cut = cut or total < ttm(dividends, shift_months(as_of, -12), include_all) - 1e-8
    if cut:
        warnings.append('최근 5년 사업연도 배당 삭감 감지' if domestic else '최근 5년 배당 삭감 감지')
    current_yield = total / current * 100
    if current_yield > quality['max_yield_pct']:
        issues.append('배당수익률 비정상')
    # Unknown classification/currency corrupts every derived dividend metric.
    invalid_div = any('배당 통화' in i or '중복 배당' in i for i in issues)
    invalid_div = invalid_div or (not include_all and any(k == 'unknown' for _, _, k in dividends))
    # An empty domestic TTM is unconfirmed, not evidence of a zero dividend.
    unknown_domestic_ttm = domestic and total == 0
    if unknown_domestic_ttm:
        issues.append('최근 12개월 국내 배당 이력 확인 필요: 무배당 미확정')
    if invalid_div:
        total = current_yield = avg3 = avg5 = None
        if not domestic:
            cagr = cut = None
        monthly3 = monthly5 = []
        warnings = [w for w in warnings if w != '최근 5년 배당 삭감 감지']
    if unknown_domestic_ttm:
        total = current_yield = None
    ratio = current_yield / avg3 if avg3 and current_yield is not None else None
    basis = '정기·특별·종류 미확인 배당 모두 포함' if include_all else '정기배당만'
    basis += ' / 배당기준일 기준' if history.get('dividend_date_basis') == 'record_date' else ' / 배당락일 기준'
    if history.get('dividend_shareholder_basis') == 'general':
        basis += ' / 일반주주 기준'
    annual_basis = '배당락일 달력연도 합계 및 TTM 감소'
    if domestic:
        annual_basis = 'DART 사업연도 총배당금(정기·특별 합계)' if include_all else 'DART 정기배당 분리 미확인'
    if domestic:
        basis += ' / 연간 성장·삭감: ' + annual_basis
    return {'ttm': total, 'yield_pct': current_yield, 'avg3_pct': avg3, 'avg5_pct': avg5,
            'ratio': ratio, 'cagr_pct': cagr, 'cut': cut, 'annual_dividends': annual,
            'record_year_dividends': record_annual,
            'annual_dividend_basis': annual_basis,
            'monthly3': monthly3, 'monthly5': monthly5,
            'missing_price_dates': missing_price_dates,
            'dividend_basis': basis,
            'dividend_date_basis': history.get('dividend_date_basis', 'ex_date'),
            'status': '정상' if not issues else '확인 필요',
            'issues': sorted(set(issues)), 'warnings': list(dict.fromkeys(warnings))}


def is_candidate(metrics, drawdown, thresholds):
    if metrics['status'] != '정상' or drawdown is None:
        return False
    def at_least(value, limit):
        return value is not None and math.isfinite(value) and (value >= limit or math.isclose(value, limit, rel_tol=1e-10, abs_tol=1e-10))
    return (math.isfinite(float(drawdown)) and float(drawdown) <= thresholds['drawdown_pct']
            and at_least(metrics['yield_pct'], thresholds['min_yield_pct'])
            and at_least(metrics['ratio'], thresholds['min_yield_ratio']))
