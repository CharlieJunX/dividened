import json
import math
from copy import deepcopy
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(path=None):
    config = json.loads((ROOT / 'backtest_config.json').read_text(encoding='utf-8-sig'))
    if path:
        custom = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        def merge(a, b):
            for key, value in b.items():
                if isinstance(value, dict) and isinstance(a.get(key), dict):
                    merge(a[key], value)
                else:
                    a[key] = value
        merge(config, custom)
    validate(config)
    return config


def validate(c):
    if date.fromisoformat(c['start']) >= date.fromisoformat(c['end']):
        raise ValueError('start must precede end')
    if c['high_basis'] not in {'high', 'close'} or c['universe_mode'] not in {'current_watchlist', 'historical'}:
        raise ValueError('Invalid high/universe policy')
    if c['holding_months'] != sorted(set(c['holding_months'])) or any(m not in (3, 6, 12) for m in c['holding_months']):
        raise ValueError('holding_months must be a nonempty subset of 3,6,12')
    if not c['holding_months']:
        raise ValueError('Empty holding periods')
    for key in ('lookback_sessions', 'max_positions', 'annualization_days', 'min_sample', 'fx_max_age_days'):
        if type(c[key]) is not int or c[key] < 1:
            raise ValueError('Invalid integer: ' + key)
    for key in ('initial_capital', 'target_weight', 'risk_free_rate'):
        if type(c[key]) not in (int, float) or not math.isfinite(c[key]):
            raise ValueError('Invalid number: ' + key)
    if c['initial_capital'] <= 0 or not 0 < c['target_weight'] <= 1 or c['risk_free_rate'] <= -1:
        raise ValueError('Invalid capital/weight/risk-free rate')
    for value in c['costs'].values():
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value < 1:
            raise ValueError('Costs must be explicit fractional assumptions in [0,1)')
    if c['reinvest_dividends'] or any(c['optional_exits'].values()):
        raise ValueError('Dividend reinvestment and optional exits are not implemented; fixed expiry only')
    if c['costs']['sell_fee'] + c['costs']['sell_tax'] >= 1:
        raise ValueError('Combined sale costs must be less than proceeds')
    if c['base_currency'] != 'KRW':
        raise ValueError('Global portfolio base currency is KRW; local currency returns are reported per event')
    for key in ('allow_overlap', 'fractional_shares', 'reinvest_dividends'):
        if type(c[key]) is not bool:
            raise ValueError('Expected boolean: ' + key)
    names = set()
    for s in c['strategies']:
        if s['id'] in names or not s['id']:
            raise ValueError('Duplicate/empty strategy')
        names.add(s['id'])
        if not -100 < s['drawdown_pct'] < 0 or set(s) - {'id', 'drawdown_pct', 'dividend', 'fundamental', 'value', 'score_min'}:
            raise ValueError('Invalid strategy')
        if s.get('score_min') is not None and not 0 <= s['score_min'] <= 100:
            raise ValueError('Invalid score threshold')
    previous = None
    for segment in c['segments']:
        a, b = segment['start'], segment['end']
        if not c['start'] <= a <= b <= c['end'] or previous and a <= previous:
            raise ValueError('Segments must be chronological, disjoint and within start/end')
        previous = b


def experiments(config):
    """Fixed, predeclared comparisons; no selection using final-test performance."""
    yield deepcopy(config)
    for threshold in config['sensitivity']['drawdown_pct']:
        c = deepcopy(config)
        c['experiment'] = 'drawdown_' + str(threshold)
        for s in c['strategies']:
            s['drawdown_pct'] = threshold
        validate(c)
        yield c
    for threshold in config['sensitivity']['score_min']:
        c = deepcopy(config)
        c['experiment'] = 'score_' + str(threshold)
        for s in c['strategies']:
            if s.get('score_min') is not None:
                s['score_min'] = threshold
        validate(c)
        yield c
