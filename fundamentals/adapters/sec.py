"""SEC entity-wide standard taxonomy facts (custom/dimensional tags stay unavailable)."""
import os
from datetime import datetime, timezone, date, timedelta
from .base import Adapter
from ..models import Fact, DataError
from ..storage import digest

GAAP = {
    'revenue': ['RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues', 'SalesRevenueNet'],
    'operating_income': ['OperatingIncomeLoss'], 'net_income': ['NetIncomeLoss', 'ProfitLoss'],
    'eps': ['EarningsPerShareDiluted'], 'ocf': ['NetCashProvidedByUsedInOperatingActivities'],
    'capex': ['PaymentsToAcquirePropertyPlantAndEquipment'],
    'cash': ['CashAndCashEquivalentsAtCarryingValue'],
    'short_debt': ['ShortTermBorrowings'], 'long_debt': ['LongTermDebtNoncurrent'],
    'current_long_debt': ['LongTermDebtCurrent'],
    'interest': ['InterestExpense'], 'assets': ['Assets'], 'equity': ['StockholdersEquity'],
    'dividends': ['PaymentsOfDividends'],
    'common_dividends': ['PaymentsOfDividendsCommonStock'], 'dps': ['CommonStockDividendsPerShareDeclared'],
    'credit_loss': ['ProvisionForLoanLeaseAndOtherLosses'],
}
IFRS = {
    'revenue': ['Revenue'], 'operating_income': ['ProfitLossFromOperatingActivities'],
    'net_income': ['ProfitLoss'], 'eps': ['DilutedEarningsLossPerShare'],
    'ocf': ['CashFlowsFromUsedInOperatingActivities'],
    'capex': ['PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities'],
    'cash': ['CashAndCashEquivalents'], 'assets': ['Assets'], 'equity': ['Equity'],
    'dividends': ['DividendsPaidClassifiedAsFinancingActivities'],
    'interest': ['InterestExpense'],
}


def parse_facts(payload, as_of, eps_verified=False, history_years=6):
    # Never mix US GAAP and IFRS in the same result.
    taxonomy = 'us-gaap' if payload.get('facts', {}).get('us-gaap') else 'ifrs-full'
    concepts = payload.get('facts', {}).get(taxonomy, {})
    mapping = GAAP if taxonomy == 'us-gaap' else IFRS
    result = []
    earliest = (date.fromisoformat(str(as_of)) - timedelta(days=366 * (history_years + 1))).isoformat()
    for metric, tags in mapping.items():
        # Use the first available tag for each period, preserving older tag histories.
        seen = set()
        for tag in tags:
            entries = concepts.get(tag, {}).get('units', {})
            for unit, facts in entries.items():
                if metric in ('eps', 'dps'):
                    valid_unit = unit.endswith('/shares')
                else:
                    valid_unit = len(unit) == 3 and unit.isupper()
                if not valid_unit:
                    continue
                available = [f for f in facts if f.get('filed', '9999') <= str(as_of)
                             and f.get('end', '9999') <= str(as_of)
                             and f.get('end', '') >= earliest
                             and f.get('form') in {'10-K', '10-Q', '10-K/A', '10-Q/A', '20-F', '20-F/A', '40-F', '40-F/A', '6-K'}]
                keys = {(f.get('start'), f['end'], unit) for f in available}
                for f in available:
                    if (f.get('start'), f['end'], unit) in seen or f.get('val') is None:
                        continue
                    accession = f.get('accn', '')
                    source = f'https://www.sec.gov/Archives/edgar/data/{int(payload["cik"])}/{accession.replace("-", "")}/'
                    result.append(Fact(metric, f['val'], f.get('start'), f['end'], f['filed'],
                                       unit, 'consolidated', taxonomy, source, accession, tag,
                                       eps_verified if metric == 'eps' else False))
                seen.update(keys)
    return result


class SEC(Adapter):
    def __init__(self, client, config):
        self.client, self.config = client, config

    def fetch(self, code, review, as_of):
        agent = os.getenv('SEC_USER_AGENT', '').strip()
        if not agent or '@' not in agent:
            raise DataError('SEC_USER_AGENT requires organization and contact email')
        headers = {'User-Agent': agent, 'Accept': 'application/json'}
        cik = review.get('cik')
        if not cik:
            mapping = self.client.json('https://www.sec.gov/files/company_tickers.json', 7 * 86400, headers)
            candidates = {str(v['cik_str']) for v in mapping.values()
                          if v['ticker'].replace('-', '.').upper() == code.replace('-', '.').upper()}
            if len(candidates) != 1:
                raise DataError('Exact SEC ticker/CIK mapping unavailable; configure cik')
            cik = candidates.pop()
        cik = str(int(cik)).zfill(10)
        sub = self.client.json(f'https://data.sec.gov/submissions/CIK{cik}.json', 86400, headers)
        if int(sub['cik']) != int(cik):
            raise DataError('SEC issuer mismatch')
        if not review.get('cik') and code.replace('.', '-').upper() not in {t.replace('.', '-').upper() for t in sub.get('tickers', [])}:
            raise DataError('SEC security identity mismatch')
        fingerprint = digest(sub.get('filings', {}).get('recent', {}))
        cache_key = f'sec-facts:{cik}:{fingerprint}'
        payload = self.client.store.get(cache_key, self.config['refresh_days'] * 86400)
        if payload is None:
            payload = self.client.json(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json', 0, headers)
            if int(payload['cik']) != int(cik):
                raise DataError('SEC facts issuer mismatch')
            self.client.store.put(cache_key, payload)
        verified = (review.get('eps_verified') is True and review.get('eps_source')
                    and review.get('reviewed_on', '9999') <= str(as_of))
        facts = parse_facts(payload, as_of, bool(verified), self.config['history_years'])
        issues = ['EPS 합산·분할 기준 미확인'] if not verified else []
        if review.get('adr') is not False and sub.get('entityType') == 'operating' and any(
                f.get('standard') == 'ifrs-full' for f in (x.dump() for x in facts)):
            issues.append('Foreign issuer/ADR: reporting currency and per-share unit require review')
        return facts, {'sic': sub.get('sic'), 'observed_on': datetime.now(timezone.utc).date().isoformat(),
                       'source': 'SEC EDGAR', 'cik': cik, 'fingerprint': fingerprint}, issues
