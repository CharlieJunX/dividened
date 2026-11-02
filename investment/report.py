import csv
import json
import logging
from pathlib import Path
from datetime import datetime
from copy import deepcopy
from fundamentals.models import Fact, DataError
from fundamentals.storage import digest
from fundamentals.report import notifications as pending_notifications
from dividend_metrics import is_candidate, currency_unit
from .growth import calculate as growth_calculation
from .price_risk import calculate as risk_calculation
from .scoring import evaluate as score_calculation
from .events import detect
from valuation.history import analyze as valuation_analysis

LOG = logging.getLogger('dividend_report')
COUNTRIES = {'kr': '한국', 'us': '미국', 'eu': '유럽'}
LABELS = {'per': 'PER', 'pbr': 'PBR', 'fcf_yield': 'FCF Yield', 'ev_ebitda': 'EV/EBITDA',
          'p_affo': 'P/AFFO', 'p_ffo': 'P/FFO', 'unclassified': '산업 미분류'}
DOMAINS = {'shareholder': '주주친화도', 'fundamental': '펀더멘털', 'valuation': '밸류에이션', 'growth': '성장성', 'price_risk': '주가 위험'}
BANDS = {'HISTORICALLY_CHEAP': '역사적 저평가 구간', 'SOMEWHAT_CHEAP': '다소 저렴한 구간',
         'NEUTRAL': '중립 구간', 'SOMEWHAT_EXPENSIVE': '다소 비싼 구간', 'HISTORICALLY_EXPENSIVE': '역사적 고평가 구간', 'N/A': '평가 불가'}


def fmt(value):
    return 'N/A' if value is None else f'{value:.2f}' if isinstance(value, (float, int)) else str(value)


def split_messages(text, maximum=3400):
    parts, current, length = [], '', 0
    for char in text:
        size = len(char.encode('utf-16-le')) // 2
        if length + size > maximum:
            parts.append(current)
            current, length = '[종합 리포트 계속]\n', 12
            length = len(current.encode('utf-16-le')) // 2
        current += char
        length += size
    if current:
        parts.append(current)
    return parts


def detail(record):
    s, v = record['score'], record['valuation']
    lines = ['[글로벌 주주친화 투자 분석]', '분석일: ' + record['as_of'],
             f'종목: {record["code"]} ({record["name"]})', '국가: ' + COUNTRIES[record['market']],
             '주주친화 등급: ' + (record['shareholder_grade'] or 'N/A'),
             '', '[가격 및 배당]', f'현재 주가: {fmt(record["current"])} {record["currency"] or "N/A"}',
             f'52주 고점 대비: {fmt(record["drawdown"])}%',
             f'현재 배당수익률: {fmt(record["yield_pct"])}%',
             f'3년 평균 배당수익률: {fmt(record["avg3_pct"])}%',
             '배당 저평가: ' + ('조건 충족' if record['dividend_undervalued'] else '미충족 또는 확인 필요'),
             '', '[밸류에이션]']
    for key, m in v['metrics'].items():
        lines += [f'{LABELS[key]}: {fmt(m["value"])} | 5년창 중앙값: {fmt(m["median5"])}',
                  f'역사적 백분위: {fmt(m["percentile"] * 100 if m["percentile"] is not None else None)}% (유효 {m["valid_months"]}개월)']
        if m['value'] is None or m['percentile'] is None:
            lines.append('미평가 사유: ' + m['reason'])
    lines += ['밸류에이션 판단: ' + BANDS[v['band']], '', '[종합 점수]']
    for domain, d in s['domains'].items():
        lines.append(f'{DOMAINS[domain]}: {fmt(d["score"])}/{d["weight"]} (평가 가능 배점 {d["available_weight"]})')
    lines += [f'획득 원점수: {fmt(s["raw_score"])}', f'100점 환산점수: {fmt(s["normalized_score"])}',
              f'위험정책 적용 총점: {fmt(s["adjusted_score"])}/100', '종합 등급: ' + (s['grade'] or 'N/A'),
              '평가 구분: ' + s['evaluation_status'], f'데이터 충족률: {fmt(s["coverage_pct"])}%',
              '펀더멘털 상태: ' + record['fundamental_status'], '최종 분류: ' + final_class(record),
              '가격 기준일: ' + (record['price_date'] or 'N/A'),
              '주요 재무 공시일: ' + (record['latest_filing'] or 'N/A')]
    if s['risk_cap_applied']:
        lines.append(f'HIGH_RISK 점수 상한 {s["policy"]["high_risk_cap"]}점 적용')
    if record.get('fundamental_missing'):
        lines.append('펀더멘털 필수 미평가: ' + ','.join(record['fundamental_missing']))
    lines += ['확인사항: ' + w for w in record['issues'] + v['warnings']]
    lines.append('등급은 상대적인 검토 우선순위이며 절대적인 매수·매도 신호가 아닙니다.')
    return '\n'.join(lines)


def final_class(record):
    if record['score']['grade'] is None:
        return '자료 확인 필요 / 종합 순위 제외'
    return {'S': '최우선 검토 후보', 'A': '우선 검토 후보', 'B': '일반 검토 후보',
            'C': '주의 후보', 'D': '위험 또는 낮은 매력도'}[record['score']['grade']]


def rank(records):
    ordered = sorted(records, key=lambda r: (r['score']['adjusted_score'] is None,
                     -(r['score']['adjusted_score'] or 0), -r['score']['coverage_pct'], r['market'], r['code']))
    all_rank = candidate_rank = 0
    for r in ordered:
        r['overall_rank'], r['candidate_rank'] = None, None
        if r['score']['rank_eligible']:
            all_rank += 1
            r['overall_rank'] = all_rank
            if r['candidate']:
                candidate_rank += 1
                r['candidate_rank'] = candidate_rank
    return ordered


def run_market(market, inputs, snapshots, dividends, financials, histories, config, dividend_config,
               store, as_of, fallback_history=None):
    quotes, divs, fins = ({r['code']: r for r in rows} for rows in (snapshots, dividends, financials))
    results = []
    for code, name, grade, _ in inputs:
        symbol = f'{market}:{code}'
        q, d, f = quotes.get(code, {}), divs.get(code, {}), fins.get(code, {})
        industry = f.get('analysis', {}).get('industry', 'UNKNOWN')
        review = {**f.get('review', {}), **config['securities'].get(symbol, {})}
        history = histories.get(code)
        issues, failed = [], False
        if history is None and fallback_history:
            try:
                key = f'investment-prices:{symbol}:{as_of}'
                history = store.get(key, 86400)
                if history is None:
                    history = fallback_history(code, market, dividend_config, as_of)
                    store.put(key, history)
            except Exception:
                issues.append('가격 이력 조회 실패')
                failed = True
        history = deepcopy(history or {'prices': [], 'splits': [], 'currency': q.get('currency'), 'basis': 'raw', 'as_of': str(as_of)})
        try:
            facts = store.merge_facts(symbol, [Fact(**x) for x in f.get('facts', [])])
            if review.get('facts_file') and review.get('facts_file') != f.get('review', {}).get('facts_file'):
                from fundamentals.adapters.official_file import load_official_file
                extra, _ = load_official_file(review['facts_file'], market, code, as_of)
                facts = store.merge_facts(symbol, extra)
            valuation = valuation_analysis(facts, history, as_of, industry, config, review, store, symbol)
            growth = growth_calculation(facts, history, as_of, industry, config, review)
            risk = risk_calculation(history, as_of, market, industry, config)
        except Exception as exc:
            # Do not carry yesterday's valid scores forward as today's successful analysis.
            issues.append('종합 분석 오류: ' + (str(exc) if isinstance(exc, DataError) else type(exc).__name__))
            LOG.warning('%s 종합 분석 오류: %s', symbol, type(exc).__name__)
            failed = True
            valuation = valuation_analysis([], {'prices': [], 'splits': [], 'currency': None}, as_of, industry, config, review)
            growth, risk = {}, {}
        fundamental = f.get('analysis', {'status': 'INSUFFICIENT_DATA'})
        price_issues = [text for text in d.get('metrics', {}).get('issues', [])
                        if any(term in text for term in ('주가', '현재가', '주당 기준', '중복 주식분할'))]
        issues.extend(price_issues)
        if q.get('current') and q.get('currency') and valuation['current']['price']['value'] is not None:
            from stock_52week import price as parse_quote_price
            quote_currency, scale = currency_unit(q['currency'])
            history_currency = currency_unit(history.get('currency'))[0]
            if quote_currency != history_currency:
                issues.append('현재가/이력 통화 불일치')
                price_issues.append('currency')
            elif abs(float(parse_quote_price(q['current'])) * scale / valuation['current']['price']['value'] - 1) * 100 > dividend_config['quality']['quote_tolerance_pct']:
                issues.append('현재가/이력 가격 불일치')
                price_issues.append('quote')
        if risk.get('volatility', {}).get('reason') == '주가 급변/분할 기준 확인 필요':
            issues.append('주가 급변/분할 기준 확인 필요')
            price_issues.append('split_basis')
        failed = failed or bool(price_issues)
        score = score_calculation(grade, fundamental, valuation, growth, risk, industry, config)
        used_filings = [e['filed'] for m in valuation['metrics'].values() for e in m['evidence'] if e.get('filed')]
        latest_filing = max(used_filings + [fundamental['latest_filing']] if fundamental.get('latest_filing') else used_filings, default=None)
        drawdown = d.get('drawdown', f.get('drawdown'))
        if drawdown is None and q.get('current') and q.get('high_52week'):
            from stock_52week import rate
            drawdown = float(rate(q['current'], q['high_52week']))
        dividend_undervalued = False
        if d.get('metrics', {}).get('status') == '정상':
            dividend_undervalued = is_candidate(d['metrics'], -100, dividend_config['thresholds'])
        price_value = valuation['current']['price']['value']
        rec = {'market': market, 'code': code, 'name': name or q.get('name', code), 'industry': industry,
               'shareholder_grade': grade, 'as_of': str(as_of), 'current': price_value,
               'currency': currency_unit(history.get('currency'))[0], 'drawdown': drawdown,
               'price_date': valuation['current']['price'].get('period_end'),
               'candidate': bool(d.get('candidate')), 'dividend_undervalued': dividend_undervalued,
               'yield_pct': d.get('metrics', {}).get('yield_pct'), 'avg3_pct': d.get('metrics', {}).get('avg3_pct'),
               'fundamental_status': fundamental['status'], 'latest_filing': latest_filing,
               'fundamental_missing': fundamental.get('missing', []),
               'fundamental_signals': [{'severity': x['severity'], 'reason': x['reason']} for x in fundamental.get('signals', [])],
               'valuation': valuation, 'growth': growth, 'price_risk': risk, 'score': score,
               'issues': issues, 'failed': failed, 'review': review,
               'policy_excluded': sorted({'per', 'pbr', 'fcf_yield', 'ev_ebitda', 'p_affo', 'p_ffo'} - set(config['industries'][industry]['valuation'])),
               'categories': {'price_drop': drawdown is not None and drawdown <= dividend_config['thresholds']['drawdown_pct'],
                              'dividend_value': dividend_undervalued,
                              'fundamental_risk': fundamental['status'] in {'WARNING', 'HIGH_RISK'},
                              'historical_value': valuation['historically_cheap'],
                              'top_score': score['grade'] in {'S', 'A'}}}
        if failed:
            # A valuation/growth/risk computation error never generates a ranked score.
            rec['score'].update(adjusted_score=None, normalized_score=None, grade=None, rank_eligible=False, evaluation_status='UNRATED')
        previous = store.previous_investment(symbol, as_of)
        events = detect(rec, previous, config)
        rec['events'] = events
        rec['initial'] = previous is None
        if events and (rec['candidate'] or not config['notifications']['events_candidate_only']):
            text = '[종합 투자 점수 변화]\n이벤트: ' + ', '.join(events) + '\n' + detail(rec)
            key = 'investment:' + digest([symbol, str(as_of), events, score['adjusted_score'], fundamental['status'], valuation['signature']])
            for index, part in enumerate(split_messages(text)):
                store.queue(f'{key}:{index}', 'investment:' + symbol, {'text': part, 'as_of': str(as_of)})
        # The independent history contains no entire daily price series or repeated monthly evidence.
        stored = deepcopy(rec)
        stored['valuation'].pop('samples', None)
        store.record_investment(symbol, stored)
        results.append(rec)
    return results


def notifications(store, records, as_of):
    return pending_notifications(store, ['investment:' + r['market'] + ':' + r['code'] for r in records], as_of)


def build_messages(records, config):
    candidates = [r for r in rank(records) if r['candidate']][:config['notifications']['max_candidates']]
    if not candidates:
        return []
    lines = ['[글로벌 주주친화 투자 분석 / 요약]', '분석일: ' + candidates[0]['as_of']]
    for r in candidates:
        s = r['score']
        lines.append(f'{r["code"]} ({r["name"]}) | {COUNTRIES[r["market"]]} | {fmt(s["adjusted_score"])}점 | {s["grade"] or "N/A"} | {r["fundamental_status"]} | {s["evaluation_status"]}')
    lines.append('데이터 부족 기업은 점수·순위 N/A. 상대적인 검토 우선순위입니다.')
    messages = split_messages('\n'.join(lines))
    if config['notifications']['detail']:
        for r in candidates:
            messages.extend(split_messages(detail(r)))
    return messages


def atomic_text(path, text, encoding='utf-8'):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(text, encoding=encoding, newline='')
    temp.replace(path)


def save(records, output_dir):
    import io
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ordered = rank(records)
    fields = ['전체 순위', '후보 순위', '국가', '종목코드', '종목명', '산업 분류', '주주친화 등급', '현재 주가',
              '통화', '52주 고점 대비 하락률', '배당 저평가 여부', '기존 투자 후보', '펀더멘털 위험 상태']
    for k in ('per', 'pbr', 'fcf_yield', 'ev_ebitda', 'p_affo', 'p_ffo'):
        fields += [k, k + ' 역사적 백분위(%)', k + ' 5년창 중앙값', k + ' 20% 분위값', k + ' 80% 분위값', k + ' 3년 중앙값', k + ' 유효 월수']
    fields += ['주주친화 점수', '펀더멘털 점수', '밸류에이션 점수', '성장성 점수', '주가 위험 점수',
               '종합 원점수', '환산 점수', '정책 적용 종합 점수', '종합 투자 등급', '데이터 충족률', '평가 구분',
               '가격 기준일', '최신 재무 공시일', '분석일', '조건 충족 내역', '산업 정책 제외 지표', '오류·확인사항']
    def row(r):
        s, v = r['score'], r['valuation']
        result = [r['overall_rank'], r['candidate_rank'], COUNTRIES[r['market']], r['code'], r['name'], r['industry'],
                  r['shareholder_grade'], r['current'], r['currency'], r['drawdown'], r['dividend_undervalued'], r['candidate'], r['fundamental_status']]
        for k in ('per', 'pbr', 'fcf_yield', 'ev_ebitda', 'p_affo', 'p_ffo'):
            m = v['metrics'].get(k, {})
            result += [m.get('value'), m['percentile'] * 100 if m.get('percentile') is not None else None,
                       m.get('median5'), m.get('q20'), m.get('q80'), m.get('median3'), m.get('valid_months')]
        result += [s['domains'][d]['score'] for d in ('shareholder', 'fundamental', 'valuation', 'growth', 'price_risk')]
        result += [s['raw_score'], s['normalized_score'], s['adjusted_score'], s['grade'], s['coverage_pct'], s['evaluation_status'],
                   r['price_date'], r['latest_filing'], r['as_of'], ','.join(k for k, val in r['categories'].items() if val), ','.join(r['policy_excluded']),
                   '; '.join(r['issues'] + v['warnings'] + [k + ': ' + m['reason'] for k, m in v['metrics'].items() if m['reason']])]
        return ['N/A' if item is None else item for item in result]
    for filename, subset in [('investment_all.csv', ordered), ('investment_candidates.csv', [r for r in ordered if r['candidate']]),
                             *[(f'friendly_{m}_investment.csv', [r for r in ordered if r['market'] == m]) for m in sorted({r['market'] for r in ordered})]]:
        buf = io.StringIO(newline='')
        writer = csv.writer(buf)
        writer.writerow(fields)
        writer.writerows(row(r) for r in subset)
        atomic_text(output_dir / filename, buf.getvalue(), 'utf-8-sig')
    # Full monthly inputs/results live in SQLite; audit records link to their signatures.
    audit = [{**r, 'valuation': {k: v for k, v in r['valuation'].items() if k != 'samples'}} for r in ordered]
    atomic_text(output_dir / 'investment.audit.json', json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False))
    failures = [{'market': r['market'], 'code': r['code'], 'issues': r['issues']} for r in records if r['failed']]
    atomic_text(output_dir / 'investment_errors.json', json.dumps(failures, ensure_ascii=False, indent=2))
    return ordered
