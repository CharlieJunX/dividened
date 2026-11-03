"""Build a bounded, provenance-aware input from existing calculated audits."""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from .evidence import load_documents


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig')) if Path(path).exists() else []


def select_records(directory, config, symbol=None, candidates=False, top=None):
    records = read(Path(directory) / 'investment.audit.json')
    if not isinstance(records, list):
        raise ValueError('Investment audit must be an array')
    selected = []
    for r in records:
        key = f"{r['market']}:{r['code']}"
        if symbol and symbol not in (key, r['code']):
            continue
        event = set(r.get('events') or ())
        triggers = config['triggers']
        chosen = (r.get('candidate') and (triggers['price_drop'] or triggers['dividend_value']) or
                  triggers['top_score'] and r.get('score', {}).get('rank_eligible') and r.get('overall_rank', 9999) <= 5 or
                  triggers['score_change'] and bool(event) or
                  triggers['fundamental_change'] and r.get('fundamental_status') in ('HIGH_RISK', 'WARNING') or
                  triggers['new_filing'] and bool(r.get('latest_filing')) and bool(event))
        if symbol or key in config['selected_symbols'] or (candidates and chosen):
            selected.append(r)
    selected.sort(key=lambda r: (not bool(r.get('candidate')), r.get('score', {}).get('adjusted_score') is None,
                                -(r.get('score', {}).get('adjusted_score') or 0), r['market'], r['code']))
    if symbol and ':' not in symbol and len(selected) > 1:
        raise ValueError('종목코드가 여러 시장에 존재합니다. market:code로 지정하세요')
    return selected[:min(top or config['max_reports_per_run'], config['max_reports_per_run'])]


def metric(evidence_id, value, unit, period, source, published_at=None, missing_reason=''):
    return {'evidence_id': evidence_id, 'value': value, 'unit': unit, 'period': period,
            'source': source, 'published_at': published_at, 'missing_reason': missing_reason if value is None else ''}


def backtest_context(directory, instrument_id, market, as_of):
    root = Path(directory)
    if not root.exists():
        return {'status': '평가 불가', 'reason': '백테스트 결과 없음'}
    for folder in sorted((x for x in root.iterdir() if x.is_dir()), reverse=True):
        try:
            summary = read(folder / 'summary.json')
            period = summary.get('period', [])
            if (summary.get('title', '').startswith('가상') or len(period) != 2 or
                    period[1] > as_of or not summary.get('run_at') or summary['run_at'][:10] > as_of):
                continue
            data = read(folder / 'input.json')
            if not any(s.get('id') == instrument_id for s in data.get('securities', [])):
                continue
            policy = read(folder / 'config.json')
            strategies = {s['id']: s for s in policy['strategies']}
            if strategies.get('dividend') != {'id': 'dividend', 'drawdown_pct': -30, 'dividend': True}:
                continue
            groups = [r for r in summary['event_summaries'] if r['sample'] == 'all' and r['strategy'] == 'dividend'
                      and r['market'] == market and r['signal_year'] == 'ALL' and r['segment'] == 'all'
                      and r['evaluation_status'] == 'EVALUABLE']
            if not groups:
                continue
            return {'status': '참고 가능', 'strategy': 'dividend', 'policy': strategies['dividend'],
                    'version': summary.get('code_sha256'), 'period': summary['period'],
                    'universe': summary['title'], 'country': market, 'horizons':
                    {str(r['months']): {k: r.get(k) for k in ('signals', 'entries', 'complete', 'net_evaluable',
                      'mean_net_pct', 'median_net_pct', 'win_rate_pct', 'mean_excess_pct')} for r in groups},
                    'limitations': summary.get('warnings', []), 'costs': policy['costs'],
                    'source_report': str(folder / 'summary.json')}
        except (ValueError, KeyError, TypeError, OSError):
            continue
    return {'status': '평가 불가', 'reason': '동일 종목·국가·전략·기간의 검증된 실거래 성과 없음'}


def build(record, output_dir, config, now=None):
    now = now or datetime.now(timezone.utc)
    as_of = record['as_of']
    if date.fromisoformat(as_of) > now.date() or (now.date() - date.fromisoformat(as_of)).days > config['max_quant_age_days']:
        raise ValueError('정량 분석 기준일이 미래이거나 허용 기간보다 오래되었습니다')
    market, code = record['market'], record['code']
    ident = config['identifiers'].get(f'{market}:{code}', {})
    quote_file = Path(output_dir) / f'friendly_{market}_52week.audit.json'
    quotes = read(quote_file)
    quote = next((q for q in quotes if q.get('code') == code), {}) if isinstance(quotes, list) else {}
    exchange = ident.get('exchange') or quote.get('exchange') or 'UNKNOWN'
    security_type = ident.get('security_type', 'ORDINARY_UNVERIFIED')
    instrument_id = f'{market}:{exchange}:{code}:{security_type}'
    price_evidence = record.get('valuation', {}).get('current', {}).get('price', {}).get('evidence') or [{}]
    price_source = price_evidence[0].get('source')
    financial = next((f for f in read(Path(output_dir) / f'friendly_{market}_fundamental.audit.json')
                      if f.get('code') == code), {})
    dividend = next((d for d in read(Path(output_dir) / f'friendly_{market}_dividend.audit.json')
                     if d.get('code') == code), {})
    source = price_source or quote.get('source') or '기존 투자 분석'
    money = record.get('currency')
    metrics = {}
    for key, value, unit, period, src, published in [
            ('price', record.get('current'), money, record.get('price_date'), source, quote.get('traded_at')),
            ('drawdown', record.get('drawdown'), '%', as_of, quote.get('source') or source, quote.get('traded_at')),
            ('dividend_yield', record.get('yield_pct'), '%', 'TTM ' + as_of, dividend.get('source'), None),
            ('dividend_3y', record.get('avg3_pct'), '%', '완료 월말 36개월', dividend.get('source'), None),
            ('investment_score', record.get('score', {}).get('adjusted_score'), '점', as_of, 'investment.scoring', as_of),
            ('coverage', record.get('score', {}).get('coverage_pct'), '%', as_of, 'investment.scoring', as_of)]:
        metrics[key] = metric('metric:' + key, value, unit, period, src or '기존 계산 모듈', published,
                              '기존 분석에서 검증 자료 부족')
    for key, val in record.get('valuation', {}).get('metrics', {}).items():
        evidence = val.get('evidence') or []
        metrics['valuation_' + key] = metric('metric:valuation_' + key, val.get('value'),
            '%' if key == 'fcf_yield' else '배', val.get('period_end'),
            evidence[0].get('source') if evidence else '기존 밸류에이션 계산',
            evidence[0].get('filed') if evidence else None, val.get('reason', ''))
    docs, rejected = load_documents(config['documents_file'], instrument_id, as_of, config['news_days'])
    latest_filing = record.get('latest_filing')
    snapshot = {'instrument_id': instrument_id, 'internal_id': f'{market}:{code}', 'market': market,
        'issuer_ids': {k: financial.get('metadata', {}).get(k) for k in ('corp_code', 'cik')
                       if financial.get('metadata', {}).get(k)},
        'exchange': exchange, 'security_type': security_type, 'code': code, 'name': record['name'],
        'reporting_currency': ident.get('reporting_currency'), 'trading_currency': money,
        'industry': record.get('industry'), 'shareholder_grade': record.get('shareholder_grade'),
        'grade_as_of': as_of, 'as_of': as_of, 'latest_filing': latest_filing,
        'metrics': metrics, 'dividend_undervalued': record.get('dividend_undervalued'),
        'dividend_cut': dividend.get('metrics', {}).get('cut'),
        'candidate': record.get('candidate'), 'risk_status': record.get('fundamental_status'),
        'risk_signals': financial.get('analysis', {}).get('signals', record.get('fundamental_signals', [])),
        'risk_missing': record.get('fundamental_missing', []),
        'valuation_band': record.get('valuation', {}).get('band'),
        'valuation_percentiles': {k: v.get('percentile') for k, v in record.get('valuation', {}).get('metrics', {}).items()},
        'investment_grade': record.get('score', {}).get('grade'),
        'evaluation_status': record.get('score', {}).get('evaluation_status'),
        'events': record.get('events', []), 'initial': record.get('initial'),
        'issues': record.get('issues', []), 'failed': record.get('failed', False),
        'quant_sources': {'investment': str(Path(output_dir) / 'investment.audit.json'),
                          'fundamental': str(Path(output_dir) / f'friendly_{market}_fundamental.audit.json'),
                          'dividend': str(Path(output_dir) / f'friendly_{market}_dividend.audit.json')},
        'documents': docs, 'document_rejections': rejected,
        'backtest': backtest_context(config['backtest_directory'], instrument_id, market, as_of)}
    if record.get('failed'):
        snapshot['issues'].append('기존 정량 분석 실패')
    return snapshot
