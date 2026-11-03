"""Optional official filing metadata adapters. Listings are never treated as full text."""
import json
import os
import hashlib
import re
import time
from datetime import date, timedelta, datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from html.parser import HTMLParser
from zoneinfo import ZoneInfo
from fundamentals.http import Client
from fundamentals.storage import Store


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.fragments = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.skip:
            self.skip -= 1

    def handle_data(self, value):
        if not self.skip and value.strip():
            self.fragments.append(value)


def sec_excerpt(document, store, agent):
    if not document['url'].startswith('https://www.sec.gov/Archives/edgar/data/'):
        raise ValueError('SEC document URL outside official archive')
    cache_key = 'ai-sec-text:' + hashlib.sha256(document['url'].encode()).hexdigest()
    cached = store.get(cache_key, 7 * 86400)
    if cached:
        return {**document, **cached}
    request = Request(document['url'], headers={'User-Agent': agent, 'Accept': 'text/html,text/plain'})
    with urlopen(request, timeout=20) as response:
        content_type = response.headers.get('Content-Type', '')
        if 'html' not in content_type and 'text/plain' not in content_type:
            return document
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        return document
    content = raw.decode('utf-8', errors='replace')
    if 'html' in content_type:
        parser = TextExtractor()
        parser.feed(content)
        content = ' '.join(parser.fragments)
    content = ' '.join(content.split())
    matches = list(re.finditer(r'(?i)dividend|revenue|earnings|guidance|liquidity|debt|cash flow', content))
    if not matches:
        return document
    offset = matches[0].start()
    excerpt = content[max(0, offset - 150):offset + 650]
    updated = {'excerpt': excerpt, 'excerpt_sha256': hashlib.sha256(excerpt.encode()).hexdigest(),
               'sha256': hashlib.sha256(raw).hexdigest(), 'full_text_verified': True}
    store.put(cache_key, updated)
    return {**document, **updated}


def sec_list(cik, instrument_id, as_of, client, limit=20):
    agent = os.getenv('SEC_USER_AGENT', '').strip()
    if '@' not in agent:
        raise ValueError('SEC_USER_AGENT requires a real organization/contact')
    cik = str(int(cik)).zfill(10)
    data = client.json(f'https://data.sec.gov/submissions/CIK{cik}.json', 86400,
                       {'User-Agent': agent, 'Accept': 'application/json'})
    if str(int(data['cik'])) != str(int(cik)):
        raise ValueError('SEC CIK mismatch')
    recent = data['filings']['recent']
    rows = []
    for i, filed in enumerate(recent['filingDate']):
        if filed > as_of or recent['form'][i] not in ('10-K', '10-Q', '8-K', '20-F', '6-K'):
            continue
        accn = recent['accessionNumber'][i]
        primary = recent['primaryDocument'][i]
        accepted = recent.get('acceptanceDateTime', [None] * len(recent['filingDate']))[i]
        parsed = datetime.fromisoformat(accepted.replace('Z', '+00:00')) if accepted else None
        published_at = (parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo('America/New_York'))).isoformat() if parsed else None
        rows.append({'id': 'sec:' + accn, 'title': recent['form'][i] + ': ' + primary,
                     'publisher': 'SEC EDGAR', 'url': f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace("-", "")}/{primary}',
                     'instrument_id': instrument_id, 'event_date': filed,
                     'published_at': published_at, 'published_date': filed,
                     'retrieved_at': datetime.now(timezone.utc).isoformat(), 'language': 'en',
                     'version': accn, 'sha256': '', 'excerpt': '', 'full_text_verified': False,
                     'source_type': 'official', 'original_document_id': accn})
        if len(rows) >= limit:
            break
    return rows


def dart_list(corp_code, instrument_id, as_of, client, api_key, days=365, limit=20):
    if not (len(corp_code) == 8 and corp_code.isdigit()):
        raise ValueError('DART corp_code must have 8 digits')
    end = date.fromisoformat(as_of)
    params = {'crtfc_key': api_key, 'corp_code': corp_code,
              'bgn_de': (end - timedelta(days=days)).strftime('%Y%m%d'),
              'end_de': end.strftime('%Y%m%d'), 'page_count': min(limit, 100), 'page_no': 1}
    data = client.json('https://opendart.fss.or.kr/api/list.json?' + urlencode(params), 86400)
    if data.get('status') == '013':
        return []
    if data.get('status') != '000':
        raise ValueError('DART listing unavailable')
    result = []
    for item in data.get('list', []):
        if item.get('corp_code') != corp_code or item.get('rcept_dt', '99999999') > end.strftime('%Y%m%d'):
            continue
        receipt = item['rcept_no']
        if len(receipt) != 14 or not receipt.isdigit():
            continue
        filed = item['rcept_dt']
        result.append({'id': 'dart:' + receipt, 'title': item['report_nm'], 'publisher': '금융감독원 OpenDART',
                       'url': 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=' + receipt,
                       'instrument_id': instrument_id, 'event_date': f'{filed[:4]}-{filed[4:6]}-{filed[6:]}',
                       'published_at': None, 'published_date': f'{filed[:4]}-{filed[4:6]}-{filed[6:]}',
                       'retrieved_at': datetime.now(timezone.utc).isoformat(), 'language': 'ko',
                       'version': receipt, 'sha256': '', 'excerpt': '', 'full_text_verified': False,
                       'source_type': 'official', 'original_document_id': receipt})
    return result[:limit]


def fetch(config, snapshots):
    store = Store(config['state_file'])
    client = Client(store, {'timeout': 20, 'attempts': 2, 'interval_seconds': .3})
    found, errors = [], []
    try:
        for s in snapshots:
            ids = {**s.get('issuer_ids', {}), **config['official_disclosures'].get(s['internal_id'], {})}
            try:
                if s['market'] == 'us' and ids.get('cik'):
                    listings = sec_list(ids['cik'], s['instrument_id'], s['as_of'], client)
                    agent = os.getenv('SEC_USER_AGENT', '')
                    for doc in listings[:3]:
                        try:
                            found.append(sec_excerpt(doc, store, agent))
                            time.sleep(.25)
                        except (OSError, ValueError):
                            found.append(doc)
                    found += listings[3:]
                elif s['market'] == 'kr' and ids.get('corp_code') and os.getenv('OPENDART_API_KEY'):
                    found += dart_list(ids['corp_code'], s['instrument_id'], s['as_of'], client,
                                       os.getenv('OPENDART_API_KEY'))
            except (OSError, ValueError) as exc:
                errors.append({'instrument_id': s['instrument_id'], 'reason': type(exc).__name__})
    finally:
        store.close()
    return found, errors
