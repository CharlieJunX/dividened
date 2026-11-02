"""Normalized import contract. Raw prices, exchange sessions and versioned evidence."""
import hashlib
import json
import math
from bisect import bisect_left, bisect_right
from datetime import date, datetime, time, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def stamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timestamp requires UTC offset')
    return result


def close_time(security, day):
    return session_time(security, day, 'close')


def session_time(security, day, kind):
    clock = security.get('session_times', {}).get(day, {}).get(kind, security[kind + '_time'])
    return datetime.combine(date.fromisoformat(day), time.fromisoformat(clock),
                            ZoneInfo(security['timezone']))


def available_day(record, security):
    """Unknown publication time: strictly next exchange session, not filing date."""
    if not all(record.get(k) for k in ('source', 'version', 'retrieved_at', 'period_end')) or not record.get('original_version_verified'):
        return None
    stamp(record['retrieved_at'])
    published = record.get('published_at')
    sessions = security['sessions']
    def at_or_after(moment):
        local_day = str(moment.astimezone(ZoneInfo(security['timezone'])).date())
        index = bisect_left(sessions, local_day)
        if index < len(sessions) and close_time(security, sessions[index]) < moment:
            index += 1
        return sessions[index] if index < len(sessions) else None
    if published:
        published = stamp(published)
        day = at_or_after(published)
    elif record.get('published_date'):
        index = bisect_right(sessions, record['published_date'])
        day = sessions[index] if index < len(sessions) else None
    else:
        return None
    if record.get('available_at') and day:
        later = stamp(record['available_at'])
        later_day = at_or_after(later)
        day = max(day, later_day) if later_day else None
    return day


def known(records, security, day):
    rows = []
    for r in records:
        effective = available_day(r, security)
        if effective and effective <= day and r['period_end'] <= day:
            published = (stamp(r['published_at']).timestamp() if r.get('published_at') else
                         datetime.combine(date.fromisoformat(r['published_date']), time(), ZoneInfo(security['timezone'])).timestamp())
            rows.append((effective, published, r))
    return [r for _, _, r in sorted(rows, key=lambda x: (x[0], x[1]))]


def coverage(security, kind, start, end):
    cert = security.get(kind + '_coverage', {})
    return bool(cert.get('verified') and cert.get('source') and cert.get('from', '9999') <= start
                and cert.get('through', '') >= end)


def load(path):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    validate(data)
    return data


def validate(data):
    if data.get('schema_version') != 1:
        raise ValueError('Expected backtest schema_version=1')
    identifiers = set()
    for s in data['securities']:
        if s['id'] in identifiers:
            raise ValueError('Duplicate security id')
        identifiers.add(s['id'])
        for key in ('name', 'market', 'currency', 'timezone', 'close_time', 'source', 'retrieved_at', 'version'):
            if not s.get(key):
                raise ValueError('Missing security metadata: ' + key)
        stamp(s['retrieved_at'])
        if s['price_basis'] != 'raw':
            raise ValueError('Execution requires raw OHLC; dividend-adjusted prices cannot be combined with dividends')
        if sorted(set(s['sessions'])) != s['sessions'] or not s['sessions']:
            raise ValueError('Exchange sessions must be unique, sorted and nonempty')
        for d in s['sessions']:
            close_time(s, d)
        dates = [b['date'] for b in s['bars']]
        if len(set(dates)) != len(dates) or any(d not in s['sessions'] for d in dates):
            raise ValueError('Duplicate bar or bar outside exchange calendar')
        for b in s['bars']:
            for k in ('open', 'high', 'close'):
                if b.get(k) is not None and not positive(b[k]):
                    raise ValueError('Invalid OHLC value')
        for a in s.get('splits', []):
            if not positive(a['ratio']):
                raise ValueError('Invalid split')
        for div in s.get('dividends', []):
            if not positive(div['amount']) or div.get('currency') != s['currency']:
                raise ValueError('Invalid dividend amount/currency')
            if div.get('pay_date') and div['pay_date'] < div['date']:
                raise ValueError('Dividend paid before entitlement')
        for collection, key in [('splits', 'date'), ('dividends', 'id')]:
            ids = [r[key] for r in s.get(collection, [])]
            if len(ids) != len(set(ids)):
                raise ValueError('Duplicate corporate action; import one resolved outcome per action')
    for currency, rows in data.get('fx', {}).items():
        if len({r['date'] for r in rows}) != len(rows) or any(not positive(r['krw_per_unit']) for r in rows):
            raise ValueError('Invalid FX series: ' + currency)


def fx(data, currency, day, max_age, at=None):
    if currency == 'KRW':
        return 1.0
    at = at or datetime.combine(date.fromisoformat(day), time.max, timezone.utc)
    def available(r):
        published = stamp(r['available_at']) if r.get('available_at') else datetime.combine(
            date.fromisoformat(r['date']) + timedelta(days=1), time(), timezone.utc)
        return published <= at
    rows = sorted((r for r in data.get('fx', {}).get(currency, []) if r['date'] <= day),
                  key=lambda r: r['date'], reverse=True)
    row = next((r for r in rows if available(r)), None)
    if not row or (date.fromisoformat(day) - date.fromisoformat(row['date'])).days > max_age:
        return None
    return row['krw_per_unit']


def write_cache(path, data):
    """Validate before atomic replacement; failed downloads never overwrite good data."""
    validate(data)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temp.replace(path)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
