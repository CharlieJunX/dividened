"""FSC/KSD cash dividend events with OpenDART annual cross-checks.

FSC V2 schema: https://www.data.go.kr/data/15043284/openapi.do
DART schema: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS002&apiId=2019005
Korean calculations explicitly use record dates, never fabricated ex-dates.
"""
import io
import json
import math
import os
import time
import zipfile
from datetime import date, datetime, timezone
from functools import lru_cache
from urllib.parse import urlencode, unquote
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

FSC_URL = 'https://apis.data.go.kr/1160100/GetStocDiviInfoService_V2/getDiviInfo_V2'
DART_URL = 'https://opendart.fss.or.kr/api/'
FSC_GUIDE = 'https://www.data.go.kr/data/15043284/openapi.do'


def request_bytes(url, params):
    """Never propagate credential-bearing URLs or untrusted server messages."""
    request = Request(url + '?' + urlencode(params), headers={'User-Agent': 'dividend-report/1.0'})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=25) as response:
                return response.read()
        except OSError:
            if attempt == 2:
                raise ValueError('국내 배당 API 연결 실패(인증키·접근권한·통신 상태 확인)') from None
            time.sleep(attempt + 1)


def request_json(url, params):
    raw = request_bytes(url, params)
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError
        return payload
    except (ValueError, UnicodeError):
        raise ValueError('국내 배당 API 응답 형식 오류(인증키·접근권한 확인)') from None


def dart_json(endpoint, key, **params):
    payload = request_json(DART_URL + endpoint + '.json', {'crtfc_key': key, **params})
    status = payload.get('status')
    if status not in {'000', '013'}:
        raise ValueError('OpenDART 조회 실패(인증키·한도·응답 상태 확인)')
    return payload


@lru_cache(maxsize=2)
def corporation_codes(key):
    raw = request_bytes(DART_URL + 'corpCode.xml', {'crtfc_key': key})
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            root = ET.fromstring(archive.read('CORPCODE.xml'))
        result = {}
        for item in root.findall('list'):
            stock_code = (item.findtext('stock_code') or '').strip()
            if stock_code:
                if stock_code in result:
                    raise ValueError('Duplicate stock code')
                result[stock_code] = item.findtext('corp_code')
        return result
    except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError):
        raise ValueError('OpenDART 법인코드 응답 확인 필요') from None


def numeric(value):
    try:
        if isinstance(value, bool):
            raise ValueError
        amount = float(str(value).replace(',', '').strip())
        if not math.isfinite(amount) or amount < 0:
            raise ValueError
        return amount
    except (ValueError, TypeError):
        raise ValueError('국내 배당금 누락/비정상 값') from None


def compact_date(value):
    text = str(value or '').strip().replace('-', '')
    try:
        if len(text) != 8 or not text.isdigit():
            raise ValueError
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError:
        raise ValueError('국내 배당 날짜 누락/비정상 값') from None


def fetch_fsc_rows(key, crno):
    rows, expected = [], None
    for page in range(1, 1001):
        payload = request_json(FSC_URL, {'serviceKey': unquote(key), 'resultType': 'json',
                                       'numOfRows': 1000, 'pageNo': page, 'crno': crno})
        response = payload.get('response', payload)
        if response.get('header', {}).get('resultCode') != '00':
            raise ValueError('금융위원회 배당 조회 실패(인증키·활용신청·한도 확인)')
        body = response.get('body', {})
        try:
            total = int(body['totalCount'])
        except (KeyError, ValueError, TypeError):
            raise ValueError('금융위원회 배당 전체 건수 확인 필요') from None
        if expected is None:
            expected = total
        if total != expected or total < 0:
            raise ValueError('금융위원회 배당 페이지 조회 중 전체 건수 변경')
        items = body.get('items') or {}
        items = items.get('item', []) if isinstance(items, dict) else items
        items = [items] if isinstance(items, dict) else items
        if not isinstance(items, list) or any(not isinstance(x, dict) for x in items):
            raise ValueError('금융위원회 배당 목록 형식 오류')
        rows.extend(items)
        if len(rows) == total:
            return rows
        if not items or len(rows) > total:
            raise ValueError('금융위원회 배당 페이지 누락/중복')
    raise ValueError('금융위원회 배당 조회 페이지 한도 초과')


def parse_fsc_rows(rows, code, crno, start, as_of):
    """Keep latest snapshot per security/record date; conflicting rows fail closed."""
    latest = {}
    for row in rows:
        isin = str(row.get('isinCd', '')).strip()
        if len(isin) != 12 or not isin.startswith('KR7') or isin[3:9] != code:
            continue  # Exact security identity, including preferred shares.
        if str(row.get('crno', '')).strip() != crno:
            raise ValueError('금융위원회 배당 법인등록번호 불일치')
        snapshot = compact_date(row.get('basDt'))
        if snapshot > as_of:
            continue
        record = compact_date(row.get('dvdnBasDt'))
        if not start <= record <= as_of:
            continue
        raw_amount = numeric(row.get('stckGenrDvdnAmt'))
        differential = row.get('stckGrdnDvdnAmt')
        differential = None if differential in (None, '', '-') else numeric(differential)
        payment_raw = row.get('cashDvdnPayDt')
        payment = compact_date(payment_raw).isoformat() if payment_raw not in (None, '', '00000000') else None
        event = {'date': record.isoformat(), 'record_date': record.isoformat(),
                 'date_basis': 'record_date', 'payment_date': payment,
                 'amount': raw_amount, 'raw_amount': raw_amount, 'kind': 'unknown',
                 'shareholder_basis': 'general', 'amount_field': 'stckGenrDvdnAmt',
                 'raw_differential_amount': differential,
                 'currency': 'KRW', 'isin': isin, 'snapshot_date': snapshot.isoformat(),
                 'reason': row.get('stckDvdnRcdNm'), 'raw': row}
        previous = latest.get(record)
        if previous is None or snapshot > compact_date(previous['snapshot_date']):
            latest[record] = event
        elif snapshot == compact_date(previous['snapshot_date']) and event != previous:
            raise ValueError('동일 기준일 배당 원본 충돌: 공시 확인 필요')
    if not latest:
        raise ValueError('금융위원회 해당 종목 배당 이력 없음: 무배당으로 확정할 수 없음')
    return [latest[day] for day in sorted(latest) if latest[day]['amount'] > 0]


def parse_dart_annual(payload, corp_code, stock_kind, as_of, amount_field='thstrm'):
    """No report or '-' means unknown, never a fabricated zero dividend."""
    if payload.get('status') == '013':
        return {'amount': None, 'reason': '정기보고서 없음', 'reason_code': 'report_missing'}
    if payload.get('status') == 'fetch_failed':
        return {'amount': None, 'reason': '사업보고서 배당 API 조회 실패', 'reason_code': 'fetch_failed'}
    matches, candidates = [], []
    for row in payload.get('list', []):
        if row.get('corp_code') != corp_code:
            raise ValueError('OpenDART 배당 법인코드 불일치')
        receipt = str(row.get('rcept_no', ''))
        if len(receipt) != 14 or not receipt.isdigit():
            raise ValueError('OpenDART 공시 접수번호 확인 필요')
        if compact_date(receipt[:8]) > as_of:
            continue
        label = ''.join(str(row.get('se', '')).split())
        if label != '주당현금배당금(원)':
            continue
        candidates.append(row)
        if str(row.get('stock_knd') or '').strip() == stock_kind:
            value = str(row.get(amount_field) or '').strip() if row.get(amount_field) != 0 else '0'
            amount = None if value in ('', '-') else numeric(value)
            info = {'amount': amount, 'receipt_no': receipt, 'amount_field': amount_field,
                            'source_url': 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=' + receipt,
                            'settlement_date': row.get('stlm_dt'), 'raw': row}
            if value == '-':
                info.update(reason='주당현금배당금이 -로 공시됨: 무배당 여부 확인 필요',
                            reason_code='dividend_amount_dash')
            elif not value:
                info.update(reason='주당현금배당금 값 누락', reason_code='amount_missing')
            matches.append(info)
    if len(matches) != 1:
        if len(matches) > 1:
            reason, reason_code = '동일 주식종류의 주당현금배당금 항목 중복', 'ambiguous_rows'
        elif candidates and all(str(r.get('stock_knd') or '').strip() in {'', '-'} for r in candidates):
            reason, reason_code = '주식종류 미표기로 보통주·우선주 식별 불가', 'stock_kind_missing'
        elif candidates:
            reason, reason_code = f'{stock_kind} 주당현금배당금 항목 없음', 'stock_kind_unmatched'
        else:
            reason, reason_code = '분석 기준일까지 주당현금배당금 항목 없음', 'dividend_item_missing'
        return {'amount': None, 'reason': reason, 'reason_code': reason_code,
                'raw_rows': candidates}
    return matches[0]


def resolve_dart_annual(payloads, corp_code, stock_kind, as_of):
    """Resolve missing share labels only from explicitly labelled later reports.

    A following report's prior-year column belongs to the target fiscal year;
    it is never substituted for that following year's current dividend.
    """
    annual = {year: parse_dart_annual(p, corp_code, stock_kind, as_of)
              for year, p in payloads.items()}
    for year, original in list(annual.items()):
        if original.get('reason_code') != 'stock_kind_missing':
            continue
        for offset, field in [(1, 'frmtrm'), (2, 'lwfr')]:
            if year + offset not in payloads:
                continue
            evidence = parse_dart_annual(payloads[year + offset], corp_code, stock_kind,
                                         as_of, amount_field=field)
            if evidence['amount'] is None:
                continue
            annual[year] = {**evidence, 'source_fiscal_year': year + offset,
                            'target_fiscal_year': year,
                            'resolution': '후속 공시의 동일 주식종류 전기·전전기 금액으로 확인',
                            'original_analysis': original}
            break
    return annual


def cross_check(events, annual, splits):
    """Informational comparison of distinct calendars, never a quality gate."""
    checks, warnings = [], []
    for year, info in annual.items():
        amount = info['amount']
        observed = sum(e['raw_amount'] for e in events if int(e['date'][:4]) == year)
        if amount is None:
            state = 'DART 확인 필요: ' + info.get('reason', '배당금 확인 근거 없음')
        elif splits:
            state = '분할 전후 주당 기준 확인 필요'
        elif math.isclose(observed, amount, rel_tol=1e-6, abs_tol=.01):
            state = '연간 금액 일치(기준일 연도/사업연도 대조)'
        else:
            # A February record date can belong to last fiscal year's final
            # dividend. Do not shift it or declare the difference missing data.
            state = '참고: 사업연도/배당기준일 연도 합계 차이(이벤트 귀속 미확정)'
        checks.append({'year': year, 'record_year_amount': observed,
                       'dart_fiscal_year_amount': amount, 'status': state,
                       'dart_reason_code': info.get('reason_code'),
                       'dart_resolution': info.get('resolution'),
                       'dart_source_url': info.get('source_url')})
        if not state.startswith('연간 금액 일치'):
            warnings.append(f'{year}년 {state}')
    return checks, warnings


def fetch_korean_history(code, config, as_of, price_fetcher):
    fsc_key = os.getenv('DATA_GO_KR_SERVICE_KEY', '').strip()
    dart_key = os.getenv('OPENDART_API_KEY', '').strip()
    if not fsc_key or not dart_key:
        raise ValueError('한국 배당 조회에 DATA_GO_KR_SERVICE_KEY 및 OPENDART_API_KEY 필요')
    settings = config.get('korean_dividends', {})
    security = settings.get('securities', {}).get(code, {})
    corp_code = security.get('corp_code') or corporation_codes(dart_key).get(code)
    if not corp_code:
        raise ValueError('OpenDART 종목-법인 매핑 없음: korean_dividends.securities 설정 필요')
    company = dart_json('company', dart_key, corp_code=corp_code)
    crno = str(company.get('jurir_no', '')).strip()
    if company.get('status') != '000' or company.get('corp_code') != corp_code or len(crno) != 13 or not crno.isdigit():
        raise ValueError('OpenDART 기업개황/법인등록번호 확인 필요')
    if company.get('stock_code') != code and not security.get('corp_code'):
        raise ValueError('OpenDART 기업개황 종목코드 불일치')
    if company.get('acc_mt') != '12':
        raise ValueError('12월 외 결산법인: 사업연도 계산 설정 확인 필요')
    stock_kind = security.get('stock_kind', '보통주')
    history = price_fetcher(code, 'kr', config, as_of)
    start = date.fromisoformat(history['requested_start'])
    rows = fetch_fsc_rows(fsc_key, crno)
    events = parse_fsc_rows(rows, code, crno, start, as_of)
    for event in events:
        # Yahoo Close is already split-adjusted; adjust only KSD raw dividends.
        factor = math.prod(float(s['ratio']) for s in history['splits']
                           if event['date'] < s['date'] <= as_of.isoformat())
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError('한국 배당 분할 조정 비율 확인 필요')
        event['amount'] = event['raw_amount'] / factor
    payloads = {}
    for year in range(as_of.year - 6, as_of.year):
        try:
            payload = dart_json('alotMatter', dart_key, corp_code=corp_code,
                                bsns_year=year, reprt_code='11011')
        except ValueError:
            # Fiscal-year metrics are optional for the yield screen. Identity
            # validation still happens in the parser, outside this boundary.
            payload = {'status': 'fetch_failed'}
        payloads[year] = payload
    annual = resolve_dart_annual(payloads, corp_code, stock_kind, as_of)
    checks, warnings = cross_check(events, annual, history['splits'])
    differential_events = [e for e in events if e['raw_differential_amount']]
    if differential_events:
        warnings.append(f'차등배당 {len(differential_events)}건: 일반주주 배당금(stckGenrDvdnAmt) 적용')
    price_source = {k: history[k] for k in ('source', 'source_url', 'retrieved_at')}
    history.update(dividends=events, dividend_date_basis='record_date',
                   dividend_shareholder_basis='general',
                   source='금융위원회·한국예탁결제원 배당 / OpenDART 검증 / Yahoo 주가',
                   source_url=FSC_GUIDE, retrieved_at=datetime.now(timezone.utc).isoformat(),
                   price_source=price_source, dividend_source={'endpoint': FSC_URL, 'crno': crno,
                   'corp_code': corp_code, 'stock_kind': stock_kind, 'row_count': len(rows)},
                   dart_annual=annual, annual_cross_checks=checks, dividend_issues=[],
                   dividend_warnings=warnings,
                   dividend_raw_rows=rows)
    return history
