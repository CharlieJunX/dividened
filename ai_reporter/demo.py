from datetime import datetime, timezone
from .input import metric


def snapshot(market='us'):
    today = datetime.now(timezone.utc).date().isoformat()
    code = {'kr': 'DEMO0001', 'us': 'DEMO', 'eu': 'DEMO.EU'}[market]
    instrument = f'{market}:DEMO_EXCHANGE:{code}:ORDINARY'
    values = {'price': (70, 'USD'), 'drawdown': (-35, '%'), 'dividend_yield': (5, '%'),
              'dividend_3y': (3, '%'), 'investment_score': (72, '점'), 'coverage': (95, '%')}
    return {'instrument_id': instrument, 'internal_id': f'{market}:{code}', 'market': market,
            'exchange': 'DEMO_EXCHANGE', 'security_type': 'ORDINARY', 'code': code,
            'name': '가상기업', 'reporting_currency': 'USD', 'trading_currency': 'USD',
            'industry': 'GENERAL', 'shareholder_grade': 'A', 'grade_as_of': today, 'as_of': today,
            'latest_filing': None, 'metrics': {k: metric('metric:' + k, v, unit, today, 'SYNTHETIC')
                                              for k, (v, unit) in values.items()},
            'dividend_undervalued': True, 'dividend_cut': False, 'candidate': True,
            'risk_status': 'NORMAL', 'risk_signals': [], 'risk_missing': [],
            'valuation_band': 'N/A', 'valuation_percentiles': {}, 'investment_grade': 'A',
            'evaluation_status': 'REGULAR', 'events': [], 'initial': True, 'issues': [], 'failed': False,
            'quant_sources': {'investment': 'SYNTHETIC'}, 'documents': [], 'document_rejections': [],
            'backtest': {'status': '평가 불가', 'reason': '가상 자료는 실제 성과가 아님'}}
