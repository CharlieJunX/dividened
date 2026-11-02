# 역사적 밸류에이션과 종합 투자 점수

기존 주가·배당·펀더멘털 분석을 유지하면서 전체 등록 종목의 평가 근거와 검토 우선순위를
저장합니다. 등급은 상대적인 검토 우선순위이며 절대적인 매수·매도 신호가 아닙니다.
기존 주주친화 등급(A/B/C)과 신규 투자 등급(S/A/B/C/D)은 별도 항목입니다.

## 실행

```powershell
# 전체 시장: 실제 조회·파일 저장, Telegram 전송 없음
.\.venv\Scripts\python.exe daily_runner.py --market all --env-file .env --dry-run

# 소수 종목 검증
.\.venv\Scripts\python.exe daily_runner.py --market kr --symbols 033780 000810 --env-file .env --dry-run

# 기존 펀더멘털 기능까지 실행하고 종합 분석만 생략
.\.venv\Scripts\python.exe daily_runner.py --market all --env-file .env --dry-run --no-investment

# 별도 점수 정책
.\.venv\Scripts\python.exe daily_runner.py --market all --env-file .env --dry-run --investment-config investment_config.json

# 오프라인 테스트
.\.venv\Scripts\python.exe -m pytest -q

# 공개 공시 기준일의 실데이터 산식 검증: 저장된 가격 이력 우선 사용
.\.venv\Scripts\python.exe validate_investment.py
# 부족한 대표 종목 가격 이력만 추가 조회
.\.venv\Scripts\python.exe validate_investment.py --fetch-missing
```

`--dry-run`은 실제 Telegram 전송을 생략합니다. 본 실행과 별도의 preview SQLite를 사용하므로
최초 분석·점수 변화·발송 상태를 소비하지 않습니다. `--no-fundamentals`는 기존 호환성을 위해
종합 점수도 생략하고 가격·배당 분석만 실행합니다.

## 계산과 시점 정합성

| 지표 | 계산 | 확인 사항 |
| --- | --- | --- |
| PER | 주가 / TTM 희석 EPS | 검증된 독립 분기 또는 최신 12개월 회계연도; EPS 0 이하 N/A |
| PBR | 보통주 시가총액 / 보통주 귀속 자본 | 우선주·비지배지분 제외 근거 필요; 자본 0 이하 N/A |
| FCF Yield | TTM(OCF − CAPEX) / 보통주 시가총액 × 100 | 지출 CAPEX는 양수 기준; 음수 FCF는 음수로 보존 |
| EV/EBITDA | (시가총액 + 전체 이자부 부채 − 현금 + 명시적 조정) / TTM EBITDA | 부채·현금·조정의 기준일·통화·범위 일치, EBITDA 양수 |
| P/AFFO·P/FFO | 보통주 시가총액 / TTM 보통주 귀속 AFFO·FFO | 정의와 귀속 범위 검증; 유사 지표로 임의 대체하지 않음 |

현재 배수 상한(PER 200, PBR 50, EV/EBITDA 100, REIT 배수 200)은 초기 이상치 선별 정책이며
설정에서 변경 가능합니다. 상한 초과는 계산값을 보존하고 역사적 저평가·점수 산정에서 제외합니다.
실제로 높은 PBR이 관측되어도 계산 오류나 기업 부실로 단정하지 않습니다.

월말 평가는 최근 완료된 60개월을 사용합니다. 주말·휴일은 해당 월말 이전의 마지막 유효
가격을 사용하며 지연 허용일은 기본 10일입니다. 미래 공시를 제외한 후 각 기간의 공개된
최신 버전을 선택하고, 누적 분기를 독립 분기로 변환합니다. 비교 불가능한 회계 범위·통화·
계정은 합산하지 않습니다. 새로운 분기 자료가 있는데 오래된 연간 실적밖에 TTM을 만들 수
없다면 과거 연간 실적을 최신 TTM처럼 대체하지 않습니다.

각 지표는 최소 36개 유효 월말 값이 있어야 백분위를 계산합니다. 최근 5년창 안의 실제 유효
개월 수와 제외 사유를 함께 저장하므로 36개를 확보했다고 완전한 60개월 이력으로 표시하지
않습니다. 3년 중앙값은 최근 36개월이 모두 유효할 때만 표시합니다. 분위값은 선형 보간을,
백분위 동률은 중간 순위(midrank)를 사용합니다. 동일한 값만 존재하는 분포는 50%입니다.

저렴함은 PER/PBR/EV 배수는 낮은 백분위, FCF Yield는 높은 백분위로 판단합니다.
음수·0 FCF Yield는 수치를 표시하지만 저평가·가치 점수 평가에서 제외합니다.
개별 지표와 종합 밸류에이션 판단을 구분합니다. 종합 역사적 저평가 후보는 해당 산업의
모든 밸류에이션 배점에 유효한 역사 분포가 있고 가중 백분위가 저렴한 쪽 20% 이내일 때입니다.
전체 영역을 평가하지 못했으면 일부 지표가 싸다는 이유만으로 종합 저평가 후보로 분류하지 않습니다.

주가의 분할 조정을 과거 시점의 원시 가격으로 되돌린 뒤 재무 수치의 주당 기준과 맞춥니다.
EPS 검증은 기존 펀더멘털 Adapter의 `eps_verified` 근거를 재사용합니다. 분석 구간에 분할이
있으면 추가적인 주당 기준 검증이 없을 때 EPS 배수·성장성은 보류합니다. 서로 다른 시점의
분할 기준이 확실하지 않으면 값을 억지로 맞추지 않습니다. 과거 PER로 역산한 EPS 수준의
구조 변화 가능성은 경고로 남기며, 분할 포함 구간은 원시 EPS 수준만으로 단정하지 않습니다.

## 데이터 출처와 보완 방법

- 기존 일별 가격·분할 이력과 배당 결과를 실행 메모리에서 공유합니다. 중복 다운로드하지 않습니다.
  배당 수집이 실패했거나 꺼져 있으면 기존 Yahoo 가격 Adapter를 통해 가격만 별도 조회하고 일별 캐시를 사용합니다.
- 미국: SEC Company Facts/Submissions를 재사용하고 실제 보통주 수(`CommonStockSharesOutstanding`)를 추가 수집합니다.
  가중평균 희석 주식 수를 시가총액 계산에 사용하지 않습니다. SEC 접근에는 기존 `SEC_USER_AGENT` 설정이 필요합니다.
- 한국: OpenDART 재무제표의 지배기업 귀속 자본을 추가 수집하고 `stockTotqySttus`에서 보통주
  발행·자기주식 차감 후 주식 수를 수집합니다. 종목·종류·기준일이 확인된 공시만 사용합니다.
- 유럽: 기존 공식 공시 JSON import를 재사용합니다. 전 유럽 자동 ESEF 수집·해석은 미지원입니다.
- 과거 공시 버전과 정규화 수치는 SQLite에 보존합니다. 현재 DART 응답에 과거 원본이 없고
  이전 저장본도 없다면 해당 과거 평가 시점은 N/A입니다. 나중에 나온 비교열을 과거 공시로 위장하지 않습니다.

출처:

- [SEC API 안내](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- [OpenDART 전체 재무제표](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020)
- [OpenDART 주식의 총수](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS002&apiId=2020002)

확인할 수 없는 값을 0으로 만들지 않습니다. `ev_adjustments`는 비지배지분·우선주·리스의
포함 여부를 명시한 검증된 조정 합계여야 합니다. 실제 확인된 0은 사용할 수 있지만 자료
누락을 0으로 처리할 수 없습니다. `equity_parent − preferred_equity`는 동일 기준의 확인된
두 금액이 있을 때만 보통주 자본으로 계산합니다. 별도 `common_equity` 공식 자료를 연결할 수도 있습니다.

종목별 추가 검증은 `investment_config.json`의 `securities`에 설정합니다. 기존 펀더멘털
설정의 검토 근거를 먼저 읽은 다음 종합 분석 설정을 덮어씁니다. 실제 검토 후에만 활성화하세요.

```json
{
  "us:EXAMPLE": {
    "source": "실제로 검토한 공식 문서 URL",
    "reviewed_on": "2026-10-28",
    "common_shares_verified": true,
    "common_equity_verified": true,
    "ev_adjustments_verified": true,
    "ev_definition": "검증한 부채·리스·우선주·비지배지분 포함 범위",
    "facts_file": "data/verified_disclosures.json"
  }
}
```

추가 파일 형식은 [FUNDAMENTALS.md](FUNDAMENTALS.md)의 `Fact` 형식과 같습니다.
보통주 수는 `unit: shares`, 자본·AFFO·EV 조정은 실제 통화 금액으로 입력합니다.
보통주 수의 최신성 허용은 기본 120일이며, 최근 공시 이후 주식 수 변동을 알 수 없는 경우
그 한계를 표시합니다. 공시 보통주 수와 가격으로 산출한 시가총액은 제공자가 공시한 시가총액과
구분한 계산값입니다. 당일의 검증된 `market_cap` 자료를 연결하면 그 값을 사용할 수 있습니다.

REIT 검증에는 `common_earnings_verified`와 `ffo_definition`이 필요합니다. 분할 구간의
주당 수치를 개별 기간 말 기준에서 맞추는 것이 확인된 경우에만 `eps_split_basis_verified`
및 `eps_basis: period_end`를 사용할 수 있습니다. 실제 검증 없이 설정으로 누락 경고를 해제하지 마세요.

## 100점 정책

| 영역 | 최대점 | 초기 정책 |
| --- | ---: | --- |
| 주주친화도 | 25 | 기존 A=25, B=18, C=10; 미등록 등급 N/A; 배당·자사주 요소 추가 가산 없음 |
| 펀더멘털 | 25 | NORMAL=25, WARNING=13, HIGH_RISK=0, INSUFFICIENT_DATA=N/A |
| 밸류에이션 | 25 | 일반: PER7/PBR4/FCF9/EV5; 은행·보험·기타 금융: PBR15/PER10; REIT: AFFO15/FFO10 |
| 성장성 | 15 | 일반: 매출/EPS/FCF 각5; 업종별 대체 지표를 별도 설정 |
| 주가 위험 | 10 | 1년 연율화 로그수익률 변동성5, 1년 종가 기준 MDD5 |

밸류에이션 점수는 낮을수록 저렴한 배수는 `배점 × (1−백분위)`, FCF Yield는 `배점 × 백분위`입니다.
성장성은 연속 4개 연간 값으로 3년 CAGR을 계산합니다. 기본 CAGR20%에서 최고점이며
양수 성장한 연도 비율로 지속성을 반영합니다. 단일 연도 100% 초과 급증은 항목 배점의
50% 상한을 적용합니다. 적자·0 이하 기준이나 일반 CAGR이 부적절한 전환은 N/A와 이유를 남깁니다.
은행·기타 금융은 순이익/EPS/ROE, 보험은 순이익/EPS/보험영업손익, REIT는 AFFO/FFO/임대수익입니다.

가격 위험은 최근 1년 200개 이상 관측·330일 이상 기간·10일 이하 가격 공백을 기본 요구합니다.
변동성은 연 252거래일로 환산합니다. 기본 선형 점수 구간은 변동성 15~60%, MDD 절댓값
10~50%입니다. `price_risk.overrides`의 `kr`, `us`, `eu` 또는 `us:BANK` 같은 키로 시장·산업별
기준을 설정할 수 있습니다. 이는 명시적 정책 구간이며 자동으로 계산한 동종업계 백분위가 아닙니다.
52주 하락률은 기존 후보 조건으로만 사용하며 점수를 가산하지 않습니다.

자료 충족률은 실제 평가 가능한 세부 항목의 배점 합계입니다. 95% 이상 REGULAR,
80~95% 미만 PROVISIONAL, 80% 미만 UNRATED입니다. 주주친화·펀더멘털·밸류에이션 영역
전체가 미평가이면 충족률과 관계없이 순위에서 제외합니다. 유효 배점 합계로 환산한 100점
점수와 유효 항목에서 획득한 원점수를 별도로 저장합니다. HIGH_RISK 환산점수는 기본 59점으로
제한합니다. S85/A70/B55/C40/D0 경계와 모든 배점은 설정할 수 있으며 산업별 합계100을 검증합니다.

## 결과·이력·알림

- `reports/investment_all.csv`: 전체 종목과 평가 가능 기업의 전체 순위.
- `reports/investment_candidates.csv`: 기존 하락률·배당 조건을 모두 충족한 종목과 후보 순위.
- `reports/friendly_{market}_investment.csv`: 시장별 결과.
- `reports/investment.audit.json`: 계산 입력·검증 근거·배점·미평가/정책 제외·분위값·제외 월말 사유.
- `reports/investment_errors.json`: 현재 실행의 수집·계산 오류. 실패를 정상 점수로 표시하지 않음.
- 기존 `reports/telegram_preview.txt`: 기존 알림과 종합 요약·상세·변화 알림 미리보기.

CSV는 UTF-8 BOM입니다. 정책상 사용하지 않는 금융/REIT 지표는 별도 정책 제외 컬럼으로
구분하며 점수의 데이터 누락으로 세지 않습니다. 부분 평가 점수와 정규·잠정·평가 불가는 구분합니다.
순위는 최종 점수 내림차순, 동점은 충족률·시장·종목코드 순입니다. N/A 기업은 순위가 없습니다.

기존 SQLite에 `financial_facts`, `valuation_months`, `investment_history`를 추가합니다.
기존 DB를 삭제하지 않으며 실행 시 테이블을 추가합니다. 월말 캐시는 해당 시점까지 알려진
자료와 가격의 해시를 사용하여 일별 가격 추가나 미래 공시 때문에 과거 60개월을 매번
새로 저장하지 않습니다. 새 정정이 과거 시점에 영향을 주지 않는 것도 테스트합니다.
오류 실행도 별도 이력으로 남겨 이전 정상 결과를 보존하며, 이전 점수를 오늘의 정상 점수로 복사하지 않습니다.

기본 종합 Telegram 대상은 기존 하락·배당 후보이고 최종 점수순으로 정렬합니다.
`notifications.max_candidates`, `detail`로 개수와 상세 출력을 설정합니다. 신규 S 진입,
A→S, 10점 이상 하락, HIGH_RISK 진입, 기존 배당 후보의 역사적 저평가 진입을 비교합니다.
기본 변화 알림도 기존 후보로 한정하고 `events_candidate_only: false`로 전체 종목에 적용할 수 있습니다.
이벤트 outbox는 기존 펀더멘털 알림과 별도 이름 공간을 쓰며 기존 발송 함수와 중복 방지
영수증을 재사용합니다. 불명확한 전송은 기존 정책대로 자동 재시도하지 않습니다.

Actions는 기존 수요일16시 KST 스케줄을 유지합니다. 매 실행마다 현재 가격·점수는 갱신하고,
재무는 기존 공시 변경 감지·주기 캐시를 재사용합니다. 신규 테이블도 같은 SQLite 안에 있으므로
기존 선택형 버전 관리 S3 백업에 함께 보관됩니다. 운영 S3가 설정되지 않았다면 실행 간 영구
이력·중복 방지는 보장되지 않습니다. 설정 방법은 [FUNDAMENTALS.md](FUNDAMENTALS.md)를 참고하세요.

## 검증과 제약

2026-10-28 최종 자동 검증: **pytest 189개 통과**(기존 146개 회귀 포함).
워크플로 YAML 문법·기존 스케줄 유지·S3 쓰기 승인 조건, CSV BOM·열 수·Windows 줄바꿈도 검사합니다.

공개 공시의 수치를 [examples/valuation_reference_disclosures.json](examples/valuation_reference_disclosures.json)에
근거·실제 공개일·단위와 함께 분리했습니다. 생산 설정에서 자동으로 읽지 않습니다.
`validate_investment.py`는 저장된 실제 가격과 독립 산식을 대조하고 성공/실패·입력·근거를 JSON으로 저장합니다.

- Apple 일반기업: 희석 EPS7.46, 보통주 자본·주식 수, OCF/CAPEX의 PER·PBR·FCF Yield 산식 대조.
- JPMorgan 은행: 조정 EPS가 아닌 보고된 희석 EPS20.02로 PER 대조.
- Allianz 보험: core EPS28.61이나 기본 EPS27.69가 아닌 희석 EPS27.67로 PER 대조.
- Realty Income REIT: 보통주 귀속 AFFO와 실제 말일 보통주 수를 이용해 P/AFFO 대조.
  희석 비지배지분을 포함한 AFFO나 가중평균 주식 수로 대체하지 않음.
- 한국 KT&G·삼성화재는 실제 dry-run으로 전체 실행 경로와 자료 부족 시 순위 제외를 확인합니다.

위 검증은 특정 공시 기준일의 산식 대조입니다. 280개 전체 종목의 현재 점수나 완전한
60개월 공식 자료가 검증되었다는 뜻이 아닙니다. 원천별 기준일과 값의 차이는 감사 파일에 남깁니다.
공식 공시를 직접 자동 파싱하지 않는 지표는 수동 검증 import가 필요합니다.
IFRS17/9, ADR 주당 단위, 복수 보통주 종류, EV 리스·우선주 조정, FFO 정의가 불명확한 경우
자동으로 채우지 않습니다. 검증된 EPS·보통주 자본·EV 자료와 과거 원본이 부족한 종목은
현재 배수 일부만 표시되거나 종합 점수가 N/A일 수 있습니다.

실수치 검증과 가상 시나리오를 구분합니다. pytest는 미래정보·정정·분기/연간 구분·결측·
음수·산업 정책·100점 상한·HIGH_RISK 상한·이력 보존·알림 중복·기존 기능을 네트워크 없이 검증합니다.
GitHub push, 실제 Telegram 전송, S3 운영 저장소 설정은 이 개발 검증에 포함하지 않습니다.

## 변경 파일

신규: `investment_config.json`, `investment/{config,growth,price_risk,scoring,events,report}.py`,
`valuation/{models,metrics,history}.py`, 두 패키지 초기화 파일, `validate_investment.py`,
`examples/valuation_reference_disclosures.json`, `test_valuation.py`, `test_investment_score.py`,
`test_investment_integration.py`, `INVESTMENT.md`.

확장: `daily_runner.py`, `dividend_report.py`, `fundamentals/storage.py`, `fundamentals/report.py`,
`fundamentals/adapters/sec.py`, `fundamentals/adapters/dart.py`, `.github/workflows/daily-stocks.yml`,
`test_fundamentals.py`(기존 기능 전용 테스트에 종합 분석 생략 옵션), `README.md`.
기존 가격·배당 계산식, 종목 목록, 펀더멘털 위험 기준 및 실행 스케줄은 유지합니다.
