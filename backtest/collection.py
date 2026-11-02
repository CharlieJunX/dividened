"""Optional read-only OHLC acquisition. Does not invent calendars, grades or filing versions."""
from datetime import date, datetime, timedelta, timezone
from math import prod
from pathlib import Path
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo
from dividend_data import parse_chart, shift_months
from dividend_metrics import currency_unit
from fundamentals.http import Client
from fundamentals.storage import Store
from .data import load, write_cache


def yahoo(symbol, market, client, start, end):
    epoch = lambda d: int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())
    query = urlencode({'period1': epoch(start), 'period2': epoch(end + timedelta(days=1)),
                       'interval': '1d', 'events': 'div,splits', 'includeAdjustedClose': 'false'})
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe="")}?{query}'
    payload = client.json(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
    h = parse_chart(payload, symbol, end)  # Reuse existing symbol/date/split parser.
    result = payload['chart']['result'][0]
    tz = ZoneInfo(h['exchange_timezone'])
    quotes = result['indicators']['quote'][0]
    currency, scale = currency_unit(h['currency'])
    bars = []
    for i, ts in enumerate(result['timestamp']):
        day = datetime.fromtimestamp(ts, tz).date().isoformat()
        if day > str(end):
            continue
        factor = prod(r['ratio'] for r in h['splits'] if r['date'] > day)
        b = {'date': day}
        for key in ('open', 'high', 'close'):
            value = quotes.get(key, [None] * len(result['timestamp']))[i]
            b[key] = value * factor * scale if value is not None else None
        bars.append(b)
    if not bars:
        raise ValueError('No OHLC observations')
    now = datetime.now(timezone.utc).isoformat()
    return {'id': market + ':' + symbol, 'name': symbol, 'market': market, 'currency': currency,
            'timezone': h['exchange_timezone'], 'open_time': {'kr': '09:00', 'us': '09:30', 'eu': '09:00'}[market],
            'close_time': {'kr': '15:30', 'us': '16:00', 'eu': '17:30'}[market],
            'price_basis': 'raw', 'source': 'Yahoo Finance chart (non-official historical price provider)',
            'version': now, 'retrieved_at': now, 'bars': bars, 'sessions': sorted(b['date'] for b in bars),
            'calendar_verified': False, 'calendar_note': 'Observed bar dates only; independent exchange calendar required for execution',
            'splits': h['splits'], 'dividends': [],
            'actions_coverage': {'verified': True, 'source': 'Yahoo chart split events; provider coverage assumption',
                                 'from': bars[0]['date'], 'through': bars[-1]['date']},
            'dividends_coverage': {'verified': False, 'source': 'No verified historical disclosure/payment timestamps'},
            'limitations': ['No independently verified trading calendar or exceptional session times',
                            'No point-in-time dividend, grade, membership or financial provenance imported']}


def collect(config):
    path = Path(config['data_file'])
    old = load(path) if path.exists() else {'schema_version': 1, 'securities': [], 'fx': {}, 'benchmarks': {}}
    existing = {s['id']: s for s in old['securities']}
    store = Store(path.parent / 'http.sqlite3')
    client = Client(store, {'timeout': 25, 'attempts': 3, 'interval_seconds': .4})
    errors = []
    try:
        for item in config.get('collect', []):
            try:
                s = yahoo(item['symbol'], item['market'], client,
                          shift_months(date.fromisoformat(config['start']), -84), date.fromisoformat(config['end']))
                # Never replace an independently reviewed import with a weaker download.
                if existing.get(s['id'], {}).get('calendar_verified'):
                    errors.append({'security': s['id'], 'reason': 'REVIEWED_IMPORT_PRESERVED'})
                else:
                    existing[s['id']] = s
            except (ValueError, OSError, KeyError, TypeError) as exc:
                errors.append({'security': item['symbol'], 'reason': type(exc).__name__, 'cache_preserved': True})
    finally:
        store.close()
    data = {**old, 'securities': list(existing.values())}
    if data['securities']:
        write_cache(path, data)
    return data, errors
