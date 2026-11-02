"""Deterministic fabricated market, explicitly not investment performance."""
from datetime import date, timedelta
from copy import deepcopy


def bundle():
    days, day = [], date(2013, 1, 1)
    while day <= date(2023, 12, 29):
        if day.weekday() < 5:
            days.append(str(day))
        day += timedelta(days=1)
    securities = []
    for key, market, currency, multiplier in [('DEMO_KR', 'kr', 'KRW', 1000), ('DEMO_US', 'us', 'USD', 1), ('DEMO_EU', 'eu', 'EUR', 1)]:
        bars = []
        for i, d in enumerate(days):
            p = 100 + (i % 80) * .05
            # Independent downturns produce false->true events and recovery.
            if '2020-02-01' <= d <= '2020-08-31' or '2021-03-01' <= d <= '2021-07-31' or d >= '2023-06-01':
                p *= .6
            bars.append({'date': d, 'open': p * multiplier, 'high': p * 1.01 * multiplier, 'close': p * multiplier})
        dividends = []
        for year in range(2013, 2024):
            for month in (3, 6, 9, 12):
                ex = next(d for d in days if d >= f'{year}-{month:02d}-10')
                dividends.append({'id': f'{year}-{month}', 'date': ex, 'amount': multiplier,
                    'pay_date': str(date.fromisoformat(ex) + timedelta(days=14)), 'currency': currency, 'kind': 'regular',
                    'period_end': str(date.fromisoformat(ex) - timedelta(days=20)),
                    'published_date': str(date.fromisoformat(ex) - timedelta(days=20)),
                    'retrieved_at': '2024-01-01T00:00:00+00:00', 'source': 'SYNTHETIC', 'version': '1',
                    'original_version_verified': True})
        securities.append({'id': key, 'name': '가상기업 ' + key, 'market': market, 'currency': currency,
            'timezone': {'kr': 'Asia/Seoul', 'us': 'America/New_York', 'eu': 'Europe/Berlin'}[market],
            'open_time': '09:30', 'close_time': '16:00', 'price_basis': 'raw', 'source': 'SYNTHETIC ONLY',
            'retrieved_at': '2024-01-01T00:00:00+00:00', 'version': 'demo-v1', 'calendar_verified': True,
            'sessions': days, 'bars': bars, 'splits': [], 'dividends': dividends,
            'actions_coverage': {'verified': True, 'source': 'SYNTHETIC', 'from': days[0], 'through': days[-1]},
            'dividends_coverage': {'verified': True, 'source': 'SYNTHETIC', 'from': days[0], 'through': days[-1]}})
    return {'schema_version': 1, 'synthetic': True, 'securities': securities,
            'fx': {c: [{'date': d, 'krw_per_unit': r} for d in days] for c, r in [('USD', 1200), ('EUR', 1400)]},
            'benchmarks': {}}


def configuration(config):
    c = deepcopy(config)
    c.update(start='2020-01-01', end='2023-12-29', segments=[], experiment='SYNTHETIC_DEMO')
    return c
