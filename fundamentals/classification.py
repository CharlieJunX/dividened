"""Only explicit classifications and unambiguous official industry codes are used."""
from datetime import date


def classify(review, metadata, as_of):
    if review.get('industry') and review.get('source') and review.get('reviewed_on', '9999') <= str(as_of):
        return review['industry'], review['source']
    # SEC SIC codes; historical metadata is not assumed to describe a past business.
    if metadata.get('observed_on', '9999') > str(as_of):
        return 'UNKNOWN', 'Industry metadata not available at analysis date'
    ksic = str(metadata.get('ksic', ''))
    if len(ksic) == 5 and ksic.isdigit():
        source = 'OpenDART KSIC ' + ksic
        if ksic.startswith('6412') or ksic == '64132':
            return 'BANK', source
        if ksic.startswith(('651', '652')):
            return 'INSURER', source
        if ksic[:2] in {'64', '65', '66'}:
            return 'OTHER_FINANCIAL', source
        # Real-estate classifications can include REIT structures: require explicit review.
        general = set(range(1, 4)) | set(range(5, 40)) | {41, 42, 45, 46, 47, 49, 50, 51, 52, 55, 56} | set(range(58, 64)) | set(range(69, 77)) | set(range(84, 97))
        if int(ksic[:2]) in general:
            return 'GENERAL', source
    sic = str(metadata.get('sic', ''))
    if len(sic) == 4 and sic.isdigit():
        n = int(sic)
        if n == 6798:
            return 'REIT', 'SEC SIC 6798'
        if 6020 <= n <= 6099:
            return 'BANK', 'SEC SIC ' + sic
        if 6310 <= n <= 6399:
            return 'INSURER', 'SEC SIC ' + sic
        if 6000 <= n <= 6999:
            return 'OTHER_FINANCIAL', 'SEC SIC ' + sic
        if 100 <= n <= 5999 or 7000 <= n <= 8999:
            return 'GENERAL', 'SEC SIC ' + sic
    return 'UNKNOWN', 'Official industry mapping unavailable; explicit review required'
