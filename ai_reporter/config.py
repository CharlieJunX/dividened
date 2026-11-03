import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(path=None):
    c = json.loads(Path(path or ROOT / 'ai_report_config.json').read_text(encoding='utf-8-sig'))
    if type(c['enabled']) is not bool or type(c['automatic']) is not bool:
        raise ValueError('Invalid switches')
    if not 1 <= c['max_reports_per_run'] <= 50 or not 1 <= c['news_days'] <= 365:
        raise ValueError('Invalid report/news limits')
    if not 1 <= c['max_quant_age_days'] <= 365:
        raise ValueError('Invalid analysis age limit')
    if any(type(v) is not bool for v in c['triggers'].values()):
        raise ValueError('Invalid trigger')
    p = c['provider']
    if p['name'] != 'openai' or not p['model'] or not p['prompt_version']:
        raise ValueError('Unsupported AI provider configuration')
    for key in ('timeout_seconds', 'retries', 'max_output_tokens', 'max_calls_per_day',
                'max_calls_per_instrument_day'):
        if type(p[key]) is not int or p[key] < 1:
            raise ValueError('Invalid AI limit: ' + key)
    if not 0 <= p['min_interval_seconds'] <= 60 or not 0 < p['max_estimated_usd_per_day'] <= 100:
        raise ValueError('Invalid AI pacing/budget')
    date.fromisoformat(p['pricing']['as_of'])
    for key in ('input_per_million_usd', 'output_per_million_usd'):
        if not 0 < p['pricing'][key] <= 1000:
            raise ValueError('Invalid price')
    if type(c['telegram']['enabled']) is not bool or not 100 <= c['telegram']['maximum_utf16_units'] <= 4096:
        raise ValueError('Invalid Telegram settings')
    return c
