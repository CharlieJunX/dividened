"""Recalculate policy inputs at each close; never backfill today's score or grade."""
from datetime import date
from math import prod
from dataclasses import replace
from dividend_metrics import calculate as dividends_calculate, is_candidate
from fundamentals.models import Fact
from fundamentals.evaluation import evaluate as fundamentals_evaluate
from investment.scoring import evaluate as score_evaluate
from investment.growth import calculate as growth_calculate
from investment.price_risk import calculate as risk_calculate
from valuation.history import analyze as value_analyze
from .data import known, available_day, coverage, positive


def history_at(s, day):
    return {'symbol': s['id'], 'currency': s['currency'], 'basis': 'raw', 'as_of': day,
            'prices': [{'date': b['date'], 'close': b.get('close')} for b in s['bars'] if b['date'] <= day],
            'dividends': [r for r in known(s.get('dividends', []), s, day) if r['date'] <= day],
            'splits': [r for r in s.get('splits', []) if r['date'] <= day],
            'source': s['source'], 'dividend_date_basis': 'ex_date'}


def drawdown(s, day, config):
    sessions = [d for d in s['sessions'] if d <= day][-config['lookback_sessions']:]
    if len(sessions) < config['lookback_sessions']:
        return None, 'INSUFFICIENT_52W_HISTORY'
    if not coverage(s, 'actions', sessions[0], day):
        return None, 'UNVERIFIED_SPLIT_HISTORY'
    bars = {b['date']: b for b in s['bars']}
    values = []
    for d in sessions:
        value = bars.get(d, {}).get(config['high_basis'])
        if not positive(value):
            return None, 'MISSING_52W_' + config['high_basis'].upper()
        factor = prod(r['ratio'] for r in s.get('splits', []) if d < r['date'] <= day)
        values.append(value / factor)
    current = bars.get(day, {}).get('close')
    if not positive(current):
        return None, 'MISSING_CLOSE'
    return (current / max(values) - 1) * 100, ''


def point(s, day, config, policies):
    dd, reason = drawdown(s, day, config)
    result = {'date': day, 'drawdown_pct': dd, 'dividend': None, 'fundamental': None,
              'historically_cheap': None, 'score': None, 'reasons': {}, 'evidence': []}
    if reason:
        result['reasons']['price'] = reason
    history = history_at(s, day)
    need_div = any(r.get('dividend') for r in config['strategies'])
    if need_div:
        all_past = [r for r in s.get('dividends', []) if r['date'] <= day]
        if (not coverage(s, 'dividends', history['prices'][0]['date'] if history['prices'] else day, day)
                or any(not available_day(r, s) or available_day(r, s) > min(day, r['date']) for r in all_past)):
            result['reasons']['dividend'] = 'UNVERIFIED_POINT_IN_TIME_DIVIDENDS'
        else:
            try:
                current = next(b['close'] for b in s['bars'] if b['date'] == day)
                dc = policies['dividend']
                metrics = dividends_calculate(history, current, s['currency'], {}, dc['quality'], dc['dividend_mode'] == 'all')
                if metrics['status'] == '정상':
                    result['dividend'] = metrics
                else:
                    result['reasons']['dividend'] = '; '.join(metrics['issues'])
            except (ValueError, KeyError, StopIteration, TypeError) as exc:
                result['reasons']['dividend'] = 'DIVIDEND_INPUT_ERROR:' + type(exc).__name__
    need_fin = any(r.get('fundamental') or r.get('value') or r.get('score_min') is not None for r in config['strategies'])
    if not need_fin:
        return result
    meta = known(s.get('classifications', []), s, day)
    industry = meta[-1]['industry'] if meta else 'UNKNOWN'
    reviews = known(s.get('reviews', []), s, day)
    review = reviews[-1]['value'] if reviews else {}
    records = known(s.get('facts', []), s, day)
    versions_by_day = {}
    for r in records:
        # Effective dates are conservative session dates. Preserve original publication in evidence.
        f = Fact(**r['fact'])
        effective = available_day(r, s)
        if f.end > r['period_end'] or f.filed > effective:
            raise ValueError('Inconsistent fact period/publication metadata')
        key = f.metric, f.start, f.end, f.basis, effective
        prior = versions_by_day.get(key)
        published = r.get('published_at', r.get('published_date'))
        if prior and prior[0] == published and prior[1].value != f.value:
            raise ValueError('Conflicting financial versions at identical publication time')
        # Timestamp order, not lexicographic accession order, wins within a session.
        versions_by_day[key] = published, replace(f, filed=effective)
        result['evidence'].append({k: r.get(k) for k in ('period_end', 'published_at', 'published_date', 'retrieved_at', 'source', 'version')})
        result['evidence'][-1]['usable_on'] = effective
    facts = [value[1] for value in versions_by_day.values()]
    if not facts or industry == 'UNKNOWN':
        result['reasons']['fundamental'] = 'NO_VERIFIED_HISTORICAL_FACTS_OR_CLASSIFICATION'
        result['reasons']['value'] = result['reasons']['score'] = 'NO_VERIFIED_HISTORICAL_INPUTS'
        return result
    asof = date.fromisoformat(day)
    fin = fundamentals_evaluate(facts, asof, industry, policies['fundamental'], review)
    if fin['status'] != 'INSUFFICIENT_DATA':
        result['fundamental'] = fin['status']
    else:
        result['reasons']['fundamental'] = '; '.join(fin['missing'] + fin['issues'])
    if any(r.get('value') or r.get('score_min') is not None for r in config['strategies']):
        # The existing valuation engine filters these effective filing dates at EACH historical month.
        val = value_analyze(facts, history, asof, industry, policies['investment'], review)
        if val['valuation_coverage'] == 100:
            result['historically_cheap'] = val['historically_cheap']
        else:
            result['reasons']['value'] = 'INCOMPLETE_HISTORICAL_VALUATION'
        grades = known(s.get('grades', []), s, day)
        grade = grades[-1]['grade'] if grades else None
        growth = growth_calculate(facts, history, asof, industry, policies['investment'], review)
        risk = risk_calculate(history, asof, s['market'], industry, policies['investment'])
        score = score_evaluate(grade, fin, val, growth, risk, industry, policies['investment'])
        result['score'] = score['adjusted_score']
        result['score_audit'] = score
        if result['score'] is None:
            result['reasons']['score'] = 'MISSING_HISTORICAL_GRADE_OR_SCORE_COMPONENTS'
    return result


def decision(p, strategy, policies):
    """Require every requested input even when an earlier condition is false."""
    required = ['price'] + [k for k in ('dividend', 'fundamental', 'value') if strategy.get(k)]
    if strategy.get('score_min') is not None:
        required.append('score')
    values = {'price': p['drawdown_pct'], 'dividend': p['dividend'], 'fundamental': p['fundamental'],
              'value': p['historically_cheap'], 'score': p['score']}
    missing = [k for k in required if values[k] is None]
    if missing:
        return None, '; '.join(p['reasons'].get(k, 'MISSING_' + k.upper()) for k in missing)
    ok = p['drawdown_pct'] <= strategy['drawdown_pct']
    if strategy.get('dividend'):
        thresholds = {**policies['dividend']['thresholds'], 'drawdown_pct': strategy['drawdown_pct']}
        ok = ok and is_candidate(p['dividend'], p['drawdown_pct'], thresholds)
    if strategy.get('fundamental'):
        ok = ok and p['fundamental'] in {'NORMAL', 'WARNING'}
    if strategy.get('value'):
        ok = ok and p['historically_cheap']
    if strategy.get('score_min') is not None:
        ok = ok and p['score'] >= strategy['score_min']
    return bool(ok), ''


def evaluate(data, config, policies):
    rows, events = [], []
    segments = config['segments'] or [{'name': 'all', 'start': config['start'], 'end': config['end']}]
    for s in data['securities']:
        for segment in segments:
            previous = {r['id']: None for r in config['strategies']}
            for day in s['sessions']:
                if not segment['start'] <= day <= segment['end']:
                    continue
                eligible = True
                if config['universe_mode'] == 'historical':
                    members = known(s.get('membership', []), s, day)
                    eligible = bool(members and members[-1]['member'])
                try:
                    p = point(s, day, config, policies)
                    ds = {r['id']: decision(p, r, policies) for r in config['strategies']}
                except (ValueError, KeyError, TypeError) as exc:
                    p = {'date': day, 'error': type(exc).__name__ + ': ' + str(exc)}
                    ds = {r['id']: (None, 'INPUT_ERROR:' + str(exc)) for r in config['strategies']}
                common = eligible and all(value is not None for value, _ in ds.values())
                for strategy in config['strategies']:
                    key = strategy['id']
                    value, reason = ds[key] if eligible else (None, 'HISTORICAL_MEMBERSHIP_NOT_CONFIRMED')
                    row = {'security': s['id'], 'market': s['market'], 'strategy': key, 'date': day,
                           'segment': segment['name'], 'segment_end': segment['end'], 'condition': value,
                           'common_eligible': common, 'reason': reason, 'point': p}
                    rows.append(row)
                    if value is True and previous[key] is not True:
                        events.append({**row, 'initial': previous[key] is None})
                    if value is not None:  # Gaps do not reset condition state.
                        previous[key] = value
    return rows, events
