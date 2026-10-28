"""Runner integration: all securities evaluated, legacy candidates untouched."""
import json
from pathlib import Path
import stock_52week as stocks
from .adapters.sec import SEC
from .adapters.dart import DART
from .adapters.europe import Europe
from .adapters.official_file import load_official_file
from .classification import classify
from .evaluation import evaluate, event
from .http import Client
from .metrics import LABELS
from .storage import digest
from .models import DataError

DECISIONS = {'NORMAL': '투자 검토 후보', 'WARNING': '주의 후보',
             'HIGH_RISK': '가치함정 위험 후보', 'INSUFFICIENT_DATA': '재무 확인 필요'}
COUNTRIES = {'kr': '한국', 'us': '미국', 'eu': '유럽'}
FIELDS = ['국가', '종목코드', '종목명', '주주친화 등급', '산업 분류', '52주 고점 대비 하락률',
          '배당 저평가 여부', '매출 성장률', '영업이익 성장률', 'EPS 성장률', '영업현금흐름', 'FCF',
          '순차입금/EBITDA', '배당 지급능력', '위험 신호 개수', '펀더멘털 위험 상태', '위험 사유',
          '데이터 원천', '데이터 신뢰 상태', '최신 재무 공시일', '분석 실행일', '투자 검토 결과',
          '매수 후보 포함', '평가 가능 항목', '미평가 항목', '분석 이벤트']


def display(value):
    if value is None:
        return 'N/A'
    if isinstance(value, bool):
        return '있음' if value else '없음'
    return f'{value:,.2f}' if isinstance(value, (float, int)) else str(value)


def message(record, kind):
    r = record['analysis']
    lines = ['[펀더멘털 위험 분석 리포트]', f'분석 이벤트: {kind}',
             f'종목: {record["code"]} ({record["name"]})', f'국가: {COUNTRIES[record["market"]]}',
             f'주주친화 등급: {record["grade"]}', f'산업 분류: {r["industry"]}',
             f'52주 고점 대비: {display(record.get("drawdown"))}%',
             f'현재 배당수익률: {display(record.get("yield_pct"))}%',
             '배당 저평가 판정: ' + ('조건 충족' if record['candidate'] else '미충족 또는 확인 필요'),
             '', '[펀더멘털 분석]']
    for key in ('revenue_growth', 'operating_income_growth', 'net_income_growth', 'eps_growth',
                'ocf', 'fcf', 'net_debt_ebitda', 'roe', 'cet1', 'npl', 'solvency',
                'affo_growth', 'dividend_affo', 'dividend_fcf', 'dividend_income'):
        m = r['metrics'].get(key)
        if m:
            suffix = '%' if key.endswith('_growth') or key in {'roe', 'cet1', 'npl', 'solvency'} else ''
            if key in {'ocf', 'fcf'} and m['evidence']:
                suffix = ' ' + m['evidence'][-1]['unit']
            if key == 'net_debt_ebitda':
                suffix = '배'
            lines.append(f'{LABELS[key]}: {display(m["value"])}{suffix} → {m["state"]}')
            if m['reason'] and m['state'] != 'N/A':
                lines.append('  ' + m['reason'])
    lines += ['', '종합 위험 상태: ' + r['status'], '위험 사유:']
    lines += [s['reason'] for s in r['signals']] or ['평가된 항목에서 위험 신호 없음']
    if r['missing']:
        lines.append('필수 미평가: ' + ', '.join(LABELS.get(k, k) for k in r['missing']))
    if r['issues']:
        lines.append('자료 확인사항: ' + '; '.join(r['issues']))
    lines += ['투자 검토 결과: ' + record['decision'], f'분석 기준일: {r["as_of"]}',
              '최신 사용 공시일: ' + (r['latest_filing'] or 'N/A'),
              '투자 의사결정 지원용이며 절대적인 기업 부실 판정이 아닙니다.']
    # Split at line boundaries; no input can overflow Telegram's UTF-16 limit.
    parts, current = [], ''
    for line in lines:
        # Untrusted long names/issues must also be bounded by codepoint-aware chunks.
        for char in line + '\n':
            if len((current + char).encode('utf-16-le')) // 2 > 3400:
                parts.append(current.rstrip())
                current = '[펀더멘털 리포트 계속]\n'
            current += char
    if current:
        parts.append(current.rstrip())
    return parts


def run_market(market, inputs, quote_audit, dividend_records, output_dir, config, store, as_of,
               adapter=None):
    client = Client(store, config['http'])
    adapter = adapter or {'us': SEC(client, config), 'kr': DART(client, config), 'eu': Europe()}[market]
    snapshots = {r['code']: r for r in quote_audit}
    dividends = {r['code']: r for r in dividend_records}
    results, errors = [], []
    for code, name, grade, _ in inputs:
        symbol = f'{market}:{code}'
        review = config['securities'].get(symbol, {})
        snapshot, div = snapshots.get(code, {}), dividends.get(code, {})
        facts, metadata, issues = [], {}, []
        try:
            facts, metadata, issues = adapter.fetch(code, review, as_of)
            if market != 'eu' and review.get('facts_file'):
                extra, extra_source = load_official_file(review['facts_file'], market, code, as_of)
                facts.extend(extra)
                metadata['supplement'] = extra_source
            industry, classification_source = classify(review, metadata, as_of)
            analysis = evaluate(facts, as_of, industry, config, review, issues, div)
        except Exception as exc:
            # Isolate providers/securities. No raw exception text (may contain credentials).
            issues = ['재무 수집·계산 실패: ' + (str(exc) if isinstance(exc, DataError) else type(exc).__name__)]
            industry, classification_source = classify(review, {}, as_of)
            analysis = evaluate([], as_of, industry, config, review, issues)
            errors.append([code, issues[0]])
        candidate = bool(div.get('candidate', False))
        drawdown = div.get('drawdown')
        if drawdown is None and snapshot.get('current') and snapshot.get('high_52week'):
            drawdown = float(stocks.rate(snapshot['current'], snapshot['high_52week']))
        record = {'market': market, 'code': code, 'name': name or snapshot.get('name', code),
                  'grade': grade, 'drawdown': drawdown, 'yield_pct': div.get('metrics', {}).get('yield_pct'),
                  'candidate': candidate, 'analysis': analysis, 'metadata': metadata,
                  'review': review,
                  'classification_source': classification_source,
                  'source': sorted({f.source for f in facts}), 'facts': [f.dump() for f in facts],
                  'buy_candidate': candidate and not (config['exclude_high_risk'] and analysis['status'] == 'HIGH_RISK'),
                  'decision': DECISIONS[analysis['status']] if candidate else '기존 배당 저평가 조건 미충족 또는 확인 필요'}
        previous = store.previous(symbol, as_of)
        kind = event(analysis, previous)
        record['event'] = kind or 'UNCHANGED'
        if kind and (kind != 'INITIAL' or candidate or analysis['signals']):
            key = digest([symbol, analysis['status'], analysis['version']])
            for index, text in enumerate(message(record, kind)):
                store.queue(f'{key}:{index}', symbol, {'text': text, 'as_of': str(as_of)})
        store.record(symbol, analysis)
        results.append(record)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = output_dir / f'friendly_{market}_fundamental'
    rows = []
    for r in results:
        a, m = r['analysis'], r['analysis']['metrics']
        val = lambda key: display(m.get(key, {}).get('value'))
        rows.append([COUNTRIES[market], r['code'], r['name'], r['grade'], a['industry'],
                     display(r['drawdown']), '충족' if r['candidate'] else '미충족 또는 확인 필요',
                     *[val(k) for k in ('revenue_growth', 'operating_income_growth', 'eps_growth', 'ocf', 'fcf', 'net_debt_ebitda')],
                     f'FCF:{val("dividend_fcf")};순이익:{val("dividend_income")}',
                     len(a['signals']), a['status'], '; '.join(
                         [s['reason'] for s in a['signals']] + a['issues'] +
                         (['필수 자료 미확인: ' + ','.join(a['missing'])] if a['missing'] else [])),
                     metadata_source(r), a['data_quality'], a['latest_filing'] or 'N/A', a['as_of'],
                     r['decision'], r['buy_candidate'],
                     ','.join(k for k, v in m.items() if v['state'] != 'N/A'),
                     ','.join(k for k, v in m.items() if v['state'] == 'N/A'), r['event']])
    stocks.write_csv(prefix.with_suffix('.csv'), FIELDS, rows)
    stocks.write_csv(prefix.with_name(prefix.name + '_errors.csv'), ['종목코드', '오류'], errors)
    prefix.with_suffix('.audit.json').write_text(json.dumps(results, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    return results, bool(errors)


def metadata_source(record):
    return '; '.join(filter(None, [record['metadata'].get('source', 'unavailable'),
                                  record['metadata'].get('supplement')]))


def notifications(store, symbols, as_of):
    return [(key, value['text']) for key, value in store.pending(set(symbols)) if value['as_of'] <= str(as_of)]


def deliver(store, pending, sender):
    for key, text in pending:
        if store.delivered(key):
            continue
        # Record before sending: ambiguous network failures must never auto-retry.
        store.delivery(key, 'ATTEMPTED')
        try:
            sender(text)
        except Exception:
            store.delivery(key, 'UNCERTAIN')
            raise
        store.delivery(key, 'SENT')
