"""OpenDART standard accounts. Initial automatic periods: December year-end only."""
import os
from datetime import date, timedelta, datetime, timezone
from urllib.parse import urlencode
from kr_dividend_data import corporation_codes
from ..models import Fact, DataError
from ..storage import digest
from dataclasses import replace

# No fuzzy account-name matching: extensions need an explicit verified mapping.
TAGS = {
    'revenue': ['ifrs-full_Revenue'],
    'operating_income': ['dart_OperatingIncomeLoss'],
    'net_income': ['ifrs-full_ProfitLoss'],
    'eps': ['ifrs-full_DilutedEarningsLossPerShare'],
    'ocf': ['ifrs-full_CashFlowsFromUsedInOperatingActivities'],
    'capex': ['ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities'],
    'cash': ['ifrs-full_CashAndCashEquivalents'],
    'short_debt': ['ifrs-full_ShorttermBorrowings'],
    'long_debt': ['dart_LongTermBorrowingsGross'],
    'current_long_debt': ['dart_CurrentPortionOfLongTermBorrowings'],
    'assets': ['ifrs-full_Assets'], 'equity': ['ifrs-full_Equity'],
    'interest': ['ifrs-full_InterestExpense'],
    'dividends': ['ifrs-full_DividendsPaidClassifiedAsFinancingActivities'],
}
INSTANT = {'cash', 'short_debt', 'long_debt', 'current_long_debt', 'assets', 'equity'}
REPORTS = {'11013': (3, 31), '11012': (6, 30), '11014': (9, 30), '11011': (12, 31)}


def amount(value):
    if value is None or str(value).strip() in {'', '-'}:
        return None
    text = str(value).strip().replace(',', '')
    if text.startswith('(') and text.endswith(')'):
        text = '-' + text[1:-1]
    return float(text)


def parse_statement(payload, year, report, scope, as_of, corp_code):
    end = date(year, *REPORTS[report]).isoformat()
    facts = []
    rows = payload.get('list', [])
    for metric, tags in TAGS.items():
        candidates = [r for r in rows if r.get('account_id') in tags
                      and r.get('sj_div') in ({'BS'} if metric in INSTANT else {'IS', 'CIS'} if metric in {'revenue', 'operating_income', 'net_income', 'eps'} else {'CF'})]
        # IS is preferred over duplicate CIS; conflicting amounts stay unavailable.
        if any(r.get('sj_div') == 'IS' for r in candidates):
            candidates = [r for r in candidates if r.get('sj_div') == 'IS']
        parsed, parsed_rows = [], []
        for r in candidates:
            if r.get('corp_code') != corp_code or r.get('reprt_code') != report or str(r.get('bsns_year')) != str(year):
                raise ValueError('OpenDART company/report mismatch')
            receipt = r.get('rcept_no', '')
            if len(receipt) != 14 or not receipt.isdigit():
                raise ValueError('Invalid OpenDART receipt')
            filed = date(int(receipt[:4]), int(receipt[4:6]), int(receipt[6:8])).isoformat()
            if filed > str(as_of) or end > str(as_of):
                continue
            value = r.get('thstrm_amount')
            if metric not in INSTANT and report != '11011' and r.get('sj_div') in {'IS', 'CIS'}:
                # DART IS quarter value is separate from YTD; only use explicit YTD.
                value = r.get('thstrm_add_amount')
            value = amount(value)
            if value is None or not r.get('currency'):
                continue
            start = None if metric in INSTANT else f'{year}-01-01'
            unit = r['currency'] + '/shares' if metric == 'eps' else r['currency']
            parsed.append(Fact(metric, value, start, end, filed, unit, scope, 'ifrs-full',
                               'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=' + receipt,
                               receipt, r['account_id']))
            parsed_rows.append(r)
        if parsed and len({(p.value, p.unit, p.filed) for p in parsed}) == 1:
            facts.append(parsed[0])
            # Later reports may restate comparatives without changing the older report API.
            # Annual columns are full fiscal years; interim IS has an explicit prior YTD field.
            base = parsed[0]
            row = parsed_rows[0]
            comparative_fields = []
            if report == '11011':
                comparative_fields = [('frmtrm_amount', 1), ('bfefrmtrm_amount', 2)]
            elif metric not in INSTANT and metric != 'eps' and row.get('sj_div') in {'IS', 'CIS'}:
                comparative_fields = [('frmtrm_add_amount', 1)]
            for field, offset in comparative_fields:
                prior = amount(row.get(field))
                if prior is not None:
                    prior_end = date(year - offset, *REPORTS[report]).isoformat()
                    prior_start = None if base.start is None else f'{year-offset}-01-01'
                    facts.append(replace(base, value=prior, start=prior_start, end=prior_end,
                                         accession=base.accession + ':' + field))
    return facts


class DART:
    def __init__(self, client, config):
        self.client, self.config = client, config

    def call(self, endpoint, key, ttl=86400, revision='', **params):
        url = 'https://opendart.fss.or.kr/api/' + endpoint + '.json?' + urlencode({'crtfc_key': key, **params})
        return self.client.json(url, ttl, namespace=revision)

    def fetch(self, code, review, as_of):
        key = os.getenv('OPENDART_API_KEY', '').strip()
        if not key:
            raise DataError('OPENDART_API_KEY required')
        corp = review.get('corp_code') or corporation_codes(key).get(code)
        if not corp:
            raise DataError('OpenDART stock/corporation mapping unavailable')
        profile = self.call('company', key, 30 * 86400, corp_code=corp)
        if profile.get('stock_code') != code:
            raise DataError('OpenDART stock identity mismatch')
        if profile.get('acc_mt') != '12':
            raise DataError('Non-December Korean fiscal years require verified period adapter')
        metadata = {'source': 'OpenDART', 'ksic': profile.get('induty_code'),
                    'observed_on': datetime.now(timezone.utc).date().isoformat(), 'corp_code': corp}
        # Daily inexpensive disclosure check. Full statements refresh only on change/TTL.
        filings = self.call('list', key, corp_code=corp, bgn_de=(as_of - timedelta(days=365)).strftime('%Y%m%d'),
                            end_de=as_of.strftime('%Y%m%d'), pblntf_ty='A', page_count=100)
        fingerprint = digest(filings)
        metadata['fingerprint'] = fingerprint
        cache_key = f'dart-facts:v2:{corp}:{self.config["history_years"]}:{fingerprint}'
        cached = self.client.store.get(cache_key, self.config['refresh_days'] * 86400)
        if cached is not None:
            return [Fact(**f) for f in cached], metadata, ['EPS 및 차입금 전체 범위는 별도 확인 필요']
        collected = []
        for scope, fs in [('consolidated', 'CFS'), ('separate', 'OFS')]:
            for year in range(as_of.year - self.config['history_years'], as_of.year + 1):
                for report in REPORTS:
                    if date(year, *REPORTS[report]) > as_of:
                        continue
                    payload = self.call('fnlttSinglAcntAll', key, self.config['refresh_days'] * 86400,
                                        revision=fingerprint, corp_code=corp, bsns_year=year, reprt_code=report, fs_div=fs)
                    collected.extend(parse_statement(payload, year, report, scope, as_of, corp))
            if collected:
                break  # Never fill gaps in consolidated histories with separate statements.
        self.client.store.put(cache_key, [f.dump() for f in collected])
        return collected, metadata, ['EPS 및 차입금 전체 범위는 별도 확인 필요']
