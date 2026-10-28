"""Configuration, per-security isolation, CSV/audit output and Telegram formatting."""
import json
import logging
from copy import deepcopy
from datetime import date
from pathlib import Path

import stock_52week as stocks
from dividend_data import fetch_history
from dividend_metrics import calculate, is_candidate, number, currency_unit

COUNTRIES = {'kr': '한국', 'us': '미국', 'eu': '유럽'}
FIELDS = ['국가', '종목코드', '종목명', '주주친화 등급', '현재 주가', '52주 고점 대비 하락률',
          '현재 배당수익률', '과거 3년 평균 배당수익률', '과거 5년 평균 배당수익률',
          '3년 평균 대비 배당수익률 배율', '최근 5년 배당성장률', '배당 삭감 여부',
          '데이터 신뢰 상태', '분석 기준일', 'TTM 주당배당금', '배당 저평가 후보',
          '위험 경고 및 확인사항', '주가 통화', 'TTM 배당금 기준 통화', '원천', '데이터 조회 시각', '배당 계산 기준']
LOG = logging.getLogger(__name__)


def load_config(path):
    defaults = Path(__file__).with_name('dividend_config.json')
    config = json.loads(defaults.read_text(encoding='utf-8-sig'))
    if Path(path).resolve() != defaults.resolve():
        custom = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if not isinstance(custom, dict):
            raise ValueError('Configuration must be an object')
        for key, value in custom.items():
            if key not in config:
                raise ValueError(f'Unknown dividend configuration: {key}')
            if key in {'thresholds', 'notifications', 'quality'}:
                if not isinstance(value, dict):
                    raise ValueError(f'{key} must be an object')
                if set(value) - set(config[key]):
                    raise ValueError(f'Unknown configuration fields in {key}')
                config[key].update(value)
            elif key == 'dividend_reviews':
                if not isinstance(value, dict):
                    raise ValueError('dividend_reviews must be an object')
                config[key].update(value)
            else:
                config[key] = value
    for key in ['enabled']:
        if type(config[key]) is not bool:
            raise ValueError(f'{key} must be boolean')
    if config['dividend_mode'] not in {'all', 'regular_only'}:
        raise ValueError('dividend_mode must be all or regular_only')
    thresholds = config['thresholds']
    for value in thresholds.values():
        if type(value) not in {int, float}:
            raise ValueError('Thresholds must be numbers')
    if not -100 <= float(thresholds['drawdown_pct']) <= 0:
        raise ValueError('drawdown_pct must be between -100 and 0')
    for key in ['min_yield_pct', 'min_yield_ratio']:
        number(thresholds[key])
    for key, value in config['quality'].items():
        if type(value) not in {int, float}:
            raise ValueError(f'{key} must be numeric')
        number(value)
        if key not in {'quote_tolerance_pct', 'max_yield_pct'} and type(value) is not int:
            raise ValueError(f'{key} must be an integer')
    if config['quality']['history_years'] < 6:
        raise ValueError('history_years must be >=6')
    n = config['notifications']
    if type(n['max_candidates']) is not int or n['max_candidates'] < 1 or type(n['notify_empty']) is not bool:
        raise ValueError('Invalid notification settings')
    if not isinstance(config['symbols'], dict) or not isinstance(config['dividend_reviews'], dict):
        raise ValueError('symbols/dividend_reviews must be objects')
    for key, review in config['dividend_reviews'].items():
        if not isinstance(review, dict):
            raise ValueError(f'Invalid dividend review: {key}')
        if key in COUNTRIES:
            if (set(review) != {'dividend_currency'}
                    or not isinstance(review['dividend_currency'], str)
                    or not review['dividend_currency'].strip()):
                raise ValueError(f'Market review must specify dividend_currency only: {key}')
            continue
        # Currency can be specified independently of a classification review.
        # Claiming a dividend kind still requires a source and dated scope.
        classification = (review.get('default_kind', 'unknown') != 'unknown'
                          or bool(review.get('events'))
                          or any(field in review for field in ['source', 'from', 'through']))
        if classification:
            if (not review.get('source') or not review.get('from') or not review.get('through')
                    or date.fromisoformat(review['from']) > date.fromisoformat(review['through'])):
                raise ValueError(f'Invalid dividend review: {key}')
        if review.get('default_kind', 'unknown') not in {'regular', 'special', 'unknown'}:
            raise ValueError(f'Invalid dividend kind: {key}')
        if review.get('date_basis', 'ex_date') not in {'ex_date', 'record_date'}:
            raise ValueError(f'Invalid dividend review date basis: {key}')
    korean = config['korean_dividends']
    if not isinstance(korean, dict) or set(korean) != {'securities'} or not isinstance(korean['securities'], dict):
        raise ValueError('korean_dividends must contain a securities object')
    for code, security in korean['securities'].items():
        if (len(code) != 6 or not code.isdigit() or not isinstance(security, dict)
                or set(security) - {'corp_code', 'stock_kind'}
                or not isinstance(security.get('corp_code'), str)
                or len(security['corp_code']) != 8 or not security['corp_code'].isdigit()
                or security.get('stock_kind', '보통주') not in {'보통주', '우선주'}):
            raise ValueError(f'Invalid Korean security mapping: {code}')
    return deepcopy(config)


def get_review(config, market, code):
    """Market currency applies to every code, including newly added stocks.

    Explicit security reviews can override defaults for exceptional securities.
    Classification evidence remains specific to the security.
    """
    reviews = config['dividend_reviews']
    review = deepcopy(reviews.get(market, {}))
    review.update(deepcopy(reviews.get(f'{market}:{code}', {})))
    return review


def display(value, decimals=2):
    return 'N/A' if value is None else f'{value:.{decimals}f}'


def cut_text(value):
    return 'N/A' if value is None else '있음' if value else '없음'


def run_market(market, inputs, quote_audit, output_dir, config, as_of):
    snapshots = {s['code']: s for s in quote_audit}
    results, audits, errors = [], [], []
    for code, name, grade, risk in inputs:
        history = None
        record = {'market': market, 'code': code, 'name': name, 'grade': grade,
                  'as_of': as_of.isoformat(), 'current': None, 'drawdown': None,
                  'currency': None, 'candidate': False, 'source': '', 'retrieved_at': '',
                  'metrics': {'ttm': None, 'yield_pct': None, 'avg3_pct': None, 'avg5_pct': None,
                              'ratio': None, 'cagr_pct': None, 'cut': None, 'status': '조회 실패',
                              'issues': [], 'warnings': []}}
        try:
            snapshot = snapshots.get(code)
            if not snapshot:
                raise ValueError('기존 현재가 조회 실패: 배당 후보 평가 불가')
            record.update(name=name or snapshot['name'], current=float(stocks.price(snapshot['current'])),
                          drawdown=float(stocks.rate(snapshot['current'], snapshot['high_52week'])),
                          currency=snapshot.get('currency'))
            history = fetch_history(code, market, config, as_of)
            record.update(source=history['source'], retrieved_at=history['retrieved_at'])
            review = get_review(config, market, code)
            metrics = calculate(history, record['current'], record['currency'], review, config['quality'],
                                include_all=config['dividend_mode'] == 'all')
            record['metrics'] = metrics
            record['candidate'] = is_candidate(metrics, record['drawdown'], config['thresholds'])
            audits.append({'code': code, 'history': history, 'review': review, 'analysis': record})
        except Exception as exc:
            # Deliberate boundary: malformed data or an adapter failure must not
            # prevent the next security or the existing drawdown alerts.
            LOG.exception('%s:%s 배당 분석 실패', market, code)
            record['metrics']['status'] = '조회 실패'
            record['metrics']['issues'] = [str(exc)]
            errors.append([code, grade, str(exc)])
            audits.append({'code': code, 'history': history, 'analysis': record, 'error': str(exc)})
        results.append(record)
        LOG.info('%s:%s 배당 분석: %s | 확인사항: %s | 경고: %s', market, code,
                 record['metrics']['status'], '; '.join(record['metrics']['issues']) or '없음',
                 '; '.join(record['metrics']['warnings']) or '없음')
    output_dir = Path(output_dir)
    rows = []
    for r in results:
        m = r['metrics']
        rows.append([COUNTRIES[market], r['code'], r['name'], r['grade'], display(r['current']),
                     display(r['drawdown']), display(m['yield_pct']), display(m['avg3_pct']),
                     display(m['avg5_pct']), display(m['ratio'], 4), display(m['cagr_pct']),
                     cut_text(m['cut']), m['status'], r['as_of'], display(m['ttm'], 6),
                     '충족' if r['candidate'] else '미충족', '; '.join(m['issues'] + m['warnings']),
                     r['currency'] or 'N/A', currency_unit(r['currency'])[0] if m['ttm'] is not None else 'N/A',
                     r['source'], r['retrieved_at'], m.get('dividend_basis', config['dividend_mode'])])
    prefix = output_dir / f'friendly_{market}_dividend'
    stocks.write_csv(prefix.with_suffix('.csv'), FIELDS, rows)
    stocks.write_csv(prefix.with_name(prefix.name + '_errors.csv'), ['종목코드', '등급', '오류'], errors)
    prefix.with_suffix('.audit.json').write_text(json.dumps(audits, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    return results, bool(errors)


def build_messages(results, config):
    candidates = sorted((r for r in results if r['candidate']), key=lambda r: r['metrics']['ratio'], reverse=True)
    if not candidates:
        return ['[배당 저평가 탐지 리포트]\n배당 저평가 후보 없음'] if config['notifications']['notify_empty'] else []
    messages = []
    for r in candidates[:config['notifications']['max_candidates']]:
        m = r['metrics']
        message = (f"[배당 저평가 탐지 리포트]\n\n국가: {COUNTRIES[r['market']]}\n"
                   f"종목: {r['code']} ({r['name']})\n주주친화 등급: {r['grade']}\n\n"
                   f"52주 고점 대비: {display(r['drawdown'], 1)}%\n"
                   f"현재 배당수익률: {display(m['yield_pct'], 1)}%\n"
                   f"3년 평균 배당수익률: {display(m['avg3_pct'], 1)}%\n"
                   f"5년 평균 배당수익률: {display(m['avg5_pct'], 1)}%\n"
                   f"3년 평균 대비: {display(m['ratio'])}배\n"
                   f"5년 배당성장률: {display(m['cagr_pct'], 1)}{'%' if m['cagr_pct'] is not None else ''}\n"
                   f"최근 배당 삭감: {cut_text(m['cut'])}\n\n판정: 배당 저평가 조건 충족\n"
                   f"분석 기준일: {r['as_of']}")
        message += '\n배당 계산 기준: ' + m.get('dividend_basis', '정기배당만')
        if m['warnings']:
            message += '\n위험 경고: ' + '; '.join(m['warnings'])
        if r.get('fundamental'):
            financial = r['fundamental']
            analysis = financial['analysis']
            message += (f'\n\n펀더멘털 위험 상태: {analysis["status"]}'
                        f'\n투자 검토 결과: {financial["decision"]}'
                        f'\n매수 후보 포함: {"예" if financial["buy_candidate"] else "아니오"}'
                        f'\n최신 재무 공시일: {analysis["latest_filing"] or "N/A"}')
        if len(message.encode('utf-16-le')) // 2 > 3500:
            raise ValueError('배당 메시지가 Telegram 크기 제한을 초과합니다')
        messages.append(message)
    return messages
