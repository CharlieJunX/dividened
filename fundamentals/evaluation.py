from .metrics import calculate, LABELS
from .storage import digest


def evaluate(facts, as_of, industry, config, review=None, source_issues=(), dividend_context=None):
    metrics, coverage, issues = calculate(facts, as_of, industry, config, review)
    context = dividend_context or {}
    dividend_metrics = context.get('metrics', {})
    # Reuse the existing module's independently verified cut result and retain its basis.
    if (metrics['dividend_cut']['value'] is None and type(dividend_metrics.get('cut')) is bool
            and context.get('as_of') == str(as_of) and context.get('source')):
        cut = dividend_metrics['cut']
        metrics['dividend_cut'] = {'value': cut, 'state': 'WARNING' if cut else 'NORMAL',
                                  'reason': '기존 배당 모듈의 삭감 판정' if cut else '',
                                  'evidence': [{'source': context['source'], 'as_of': context['as_of'],
                                                'basis': dividend_metrics.get('annual_dividend_basis'),
                                                'annual_dividends': dividend_metrics.get('annual_dividends')}]}
    missing = [key for key in config['industries'][industry]['required']
               if metrics[key]['state'] == 'N/A' and not metrics[key].get('evaluated')]
    signals = []
    for key, m in metrics.items():
        if m['state'] in {'WARNING', 'SEVERE'}:
            signals.append({'id': key, 'severity': m['state'], 'reason': m['reason'] or LABELS.get(key, key),
                            'value': m['value'], 'evidence': m['evidence']})
    severe = sum(s['severity'] == 'SEVERE' for s in signals)
    warning = sum(s['severity'] == 'WARNING' for s in signals)
    if missing or issues or industry == 'UNKNOWN':
        status = 'INSUFFICIENT_DATA'
    elif severe >= 2:
        status = 'HIGH_RISK'
    elif severe or warning >= 2:
        status = 'WARNING'
    elif warning:
        status = config['single_warning_status']
    else:
        status = 'NORMAL'
    filings = [f.filed for f in facts if f.filed <= str(as_of)]
    return {'as_of': str(as_of), 'industry': industry, 'status': status, 'metrics': metrics,
            'coverage': coverage, 'missing': missing, 'issues': list(source_issues) + issues,
            'signals': signals, 'severe_count': severe, 'warning_count': warning,
            'risk_score': 2 * severe + warning, 'latest_filing': max(filings) if filings else None,
            'policy': config['industries'][industry],
            'quality_policy': {key: config[key] for key in ('min_annual_periods', 'min_quarter_periods',
                                'max_annual_age_days', 'max_quarter_age_days', 'single_warning_status')},
            'data_quality': 'COMPLETE' if not missing and not issues and industry != 'UNKNOWN' else 'INCOMPLETE',
            'version': digest({'config': config, 'facts': [f.dump() for f in facts]})}


def event(result, previous):
    """No transition across a gap in evidence; severe additions remain independently visible."""
    if previous is None:
        return 'INITIAL'
    old = {s['id'] for s in previous['signals'] if s['severity'] == 'SEVERE'}
    new = {s['id'] for s in result['signals'] if s['severity'] == 'SEVERE'}
    if (previous['status'], result['status']) in {('NORMAL', 'WARNING'), ('WARNING', 'HIGH_RISK'), ('NORMAL', 'HIGH_RISK')}:
        return 'UPGRADE'
    if new - old:
        return 'NEW_SEVERE'
    return None
