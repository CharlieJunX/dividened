import json
import math
from datetime import date
from pathlib import Path

KINDS = {'GENERAL', 'BANK', 'INSURER', 'REIT', 'OTHER_FINANCIAL', 'UNKNOWN'}


def load_config(path=None):
    path = Path(path) if path else Path(__file__).resolve().parent.parent / 'fundamental_config.json'
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    for key in ('enabled', 'exclude_high_risk'):
        if type(config[key]) is not bool:
            raise ValueError(f'{key} must be boolean')
    for key in ('refresh_days', 'max_annual_age_days', 'max_quarter_age_days', 'history_years',
                'min_annual_periods', 'min_quarter_periods'):
        if type(config[key]) is not int or config[key] < 1:
            raise ValueError(f'Invalid {key}')
    if config['min_annual_periods'] < 5 or config['min_quarter_periods'] < 8 or config['history_years'] < 5:
        raise ValueError('At least five annual and eight quarterly periods required')
    if config['single_warning_status'] not in {'WARNING', 'INSUFFICIENT_DATA'}:
        raise ValueError('A single warning cannot be NORMAL')
    for name, value in config['http'].items():
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f'Invalid HTTP setting: {name}')
    if type(config['http']['attempts']) is not int:
        raise ValueError('HTTP attempts must be an integer')
    if set(config['industries']) != KINDS:
        raise ValueError('All industry policies required')
    for policy in config['industries'].values():
        if not isinstance(policy['required'], list) or not policy['required']:
            raise ValueError('Nonempty required metrics list required')
        for metric, bounds in policy['thresholds'].items():
            if (len(bounds) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in bounds)):
                raise ValueError(f'Invalid thresholds: {metric}')
    for symbol, review in config['securities'].items():
        if review.get('industry', 'UNKNOWN') not in KINDS:
            raise ValueError(f'Invalid industry: {symbol}')
        if review.get('industry', 'UNKNOWN') != 'UNKNOWN' and not (review.get('source') and review.get('reviewed_on')):
            raise ValueError(f'Classification requires source and reviewed_on: {symbol}')
        if review.get('reviewed_on'):
            date.fromisoformat(review['reviewed_on'])
        for flag in ('eps_verified', 'dividend_split_verified', 'debt_components_complete', 'adr'):
            if flag in review and type(review[flag]) is not bool:
                raise ValueError(f'{flag} must be boolean: {symbol}')
        if any(review.get(flag) for flag in ('eps_verified', 'dividend_split_verified', 'debt_components_complete')):
            if not review.get('source') or not review.get('reviewed_on'):
                raise ValueError(f'Financial review evidence required: {symbol}')
        if review.get('eps_verified') and not review.get('eps_source'):
            raise ValueError(f'EPS verification source required: {symbol}')
    return config
