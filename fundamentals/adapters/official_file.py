"""Explicitly reviewed normalized disclosures, including provider-specific metrics."""
import json
from pathlib import Path
from ..models import Fact, DataError


def load_official_file(path, market, code, as_of):
    payload = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if payload.get('code') != code or payload.get('market') != market:
        raise DataError('Official import security identity mismatch')
    facts = [Fact(**f) for f in payload['facts']]
    if any(not f.source.startswith('https://') for f in facts):
        raise DataError('Disclosure imports require source URLs per fact')
    return [f for f in facts if f.filed <= str(as_of)], payload.get('source', 'reviewed disclosure import')
