"""Verified normalized official disclosure import; no synthetic pan-European feed."""
from .official_file import load_official_file


class Europe:
    def fetch(self, code, review, as_of):
        path = review.get('facts_file')
        if not path:
            return [], {'source': 'unsupported'}, ['유럽 공식 재무자료 미연결: 검증된 facts_file 필요']
        facts, source = load_official_file(path, 'eu', code, as_of)
        return facts, {'source': source}, ['수동 연결 공시: 원문·계정 매핑 검증 필요']
