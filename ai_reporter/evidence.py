"""Source records are independent of model text. A title is not document contents."""
import hashlib
import json
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse


def when(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Published/retrieved timestamps require a timezone')
    return parsed


def norm(text):
    return ' '.join(str(text).casefold().split())


def validate_document(doc, instrument_id, as_of, observed_at=None):
    needed = ('id', 'title', 'publisher', 'url', 'instrument_id', 'event_date', 'published_at',
              'retrieved_at', 'language', 'version', 'sha256', 'excerpt', 'full_text_verified', 'source_type')
    if not isinstance(doc, dict) or any(k not in doc for k in needed):
        return 'DOCUMENT_FIELDS_MISSING'
    if doc['instrument_id'] != instrument_id:
        return 'OTHER_COMPANY_DOCUMENT'
    url = urlparse(doc['url'])
    if url.scheme != 'https' or not url.netloc:
        return 'DOCUMENT_URL_INVALID'
    published = when(doc['published_at']) if doc['published_at'] else when(doc['published_date'] + 'T23:59:59+00:00')
    cutoff = min(when(as_of + 'T23:59:59+00:00'), observed_at or datetime.now(timezone.utc))
    if published > cutoff or date.fromisoformat(doc['event_date']) > date.fromisoformat(as_of):
        return 'FUTURE_DOCUMENT'
    if when(doc['retrieved_at']) < published:
        return 'IMPOSSIBLE_RETRIEVAL_TIME'
    if type(doc['full_text_verified']) is not bool or doc['source_type'] not in ('official', 'ir', 'news', 'other'):
        return 'DOCUMENT_TYPE_INVALID'
    if doc['excerpt'] and (not doc['full_text_verified'] or
                           hashlib.sha256(doc['excerpt'].encode()).hexdigest() != doc.get('excerpt_sha256') or
                           not isinstance(doc['sha256'], str) or len(doc['sha256']) != 64):
        return 'UNVERIFIED_EXCERPT_OR_HASH'
    if not doc['excerpt'] and doc['full_text_verified']:
        return 'EMPTY_VERIFIED_EXCERPT'
    return None


def load_documents(path, instrument_id, as_of, news_days=30):
    path = Path(path)
    rows = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else []
    if type(rows) is not list:
        raise ValueError('Document import must be a list')
    accepted, rejected, dedup = [], [], set()
    cutoff = date.fromisoformat(as_of) - timedelta(days=news_days)
    for doc in sorted(rows, key=lambda d: (d.get('published_at', ''), d.get('id', ''))):
        try:
            reason = validate_document(doc, instrument_id, as_of)
        except (ValueError, TypeError, KeyError):
            reason = 'INVALID_DOCUMENT_TIMESTAMP'
        if not reason and doc['source_type'] == 'news' and date.fromisoformat(doc['event_date']) < cutoff:
            reason = 'NEWS_OUTSIDE_LOOKBACK_OR_REPUBLICATION'
        identity = doc.get('original_document_id') or doc.get('canonical_url') or doc.get('sha256')
        if not reason and identity in dedup:
            reason = 'DUPLICATE_OR_SYNDICATED_DOCUMENT'
        if reason:
            rejected.append({'id': doc.get('id'), 'reason': reason})
        else:
            dedup.add(identity)
            accepted.append({**doc, 'excerpt': doc['excerpt'][:1200]})
    accepted.sort(key=lambda d: ({'official': 0, 'ir': 1, 'news': 2, 'other': 3}[d['source_type']],
                                  -(when(d['published_at']).timestamp() if d['published_at'] else date.fromisoformat(d['published_date']).toordinal())))
    return accepted, rejected
