"""Market routing and Yahoo price/foreign-dividend adapter.

Close is split-adjusted, NOT dividend-adjusted. Yahoo dividend events use the
same split basis. Never apply splits again; retain actions for audit/review.
Domestic dividends are replaced by FSC/KSD events with OpenDART cross-checks.
Yahoo does not reliably supply dividend type/currency: these remain unverified.
"""
import calendar
import json
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


def shift_months(day, months):
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def fetch_history(code, market, config, as_of):
    """Domestic dividends use official Korean sources; prices retain Yahoo Close."""
    if market == 'kr':
        from kr_dividend_data import fetch_korean_history
        return fetch_korean_history(code, config, as_of, fetch_yahoo_history)
    return fetch_yahoo_history(code, market, config, as_of)


def fetch_yahoo_history(code, market, config, as_of):
    """Fetch >=6 years in one request; no fallback to a different security."""
    symbol = config['symbols'].get(f'{market}:{code}', code + '.KS' if market == 'kr' else code)
    start = shift_months(as_of, -12 * config['quality']['history_years']) - timedelta(days=15)
    epoch = lambda d: int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())
    query = urlencode({'period1': epoch(start), 'period2': epoch(as_of + timedelta(days=1)),
                       'interval': '1d', 'events': 'div,splits', 'includeAdjustedClose': 'false'})
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe="")}?{query}'
    request = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=25) as response:
                payload = json.load(response)
            history = parse_chart(payload, symbol, as_of)
            history.update(source='Yahoo Finance chart', source_url=url,
                           retrieved_at=datetime.now(timezone.utc).isoformat(), requested_start=start.isoformat())
            return history
        except (OSError, ValueError, KeyError, TypeError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def parse_chart(payload, symbol, as_of):
    chart = payload['chart']
    if chart.get('error') or not chart.get('result'):
        raise ValueError(f'Yahoo history unavailable: {chart.get("error")}')
    result = chart['result'][0]
    meta = result['meta']
    if meta['symbol'].casefold() != symbol.casefold():
        raise ValueError('History symbol mismatch')
    # Exchange-local dates prevent UTC offsets from moving ex-dates by a day.
    tz = ZoneInfo(meta['exchangeTimezoneName'])
    local_date = lambda ts: datetime.fromtimestamp(int(ts), tz).date()
    timestamps = result['timestamp']
    closes = result['indicators']['quote'][0]['close']
    if len(timestamps) != len(closes):
        raise ValueError('Price timestamp/close length mismatch')
    prices = [{'date': local_date(ts).isoformat(), 'close': close}
              for ts, close in zip(timestamps, closes) if local_date(ts) <= as_of]
    events = result.get('events', {})
    dividends = [{'date': local_date(v['date']).isoformat(), 'amount': v['amount'],
                  'kind': 'unknown', 'currency': v.get('currency')}
                 for v in events.get('dividends', {}).values() if local_date(v['date']) <= as_of]
    splits = [{'date': local_date(v['date']).isoformat(),
               'ratio': float(v['numerator']) / float(v['denominator'])}
              for v in events.get('splits', {}).values() if local_date(v['date']) <= as_of]
    return {'symbol': symbol, 'prices': prices, 'dividends': dividends, 'splits': splits,
            'currency': meta.get('currency'), 'basis': 'split_adjusted',
            'as_of': as_of.isoformat(), 'exchange_timezone': meta['exchangeTimezoneName']}
