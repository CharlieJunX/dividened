# 글로벌 기업 펀더멘털 악화 탐지

기존 52주 하락 알림과 배당 저평가 조건은 유지합니다. 등록된 한국·미국·유럽 모든 종목을
분석하고, 기존 배당 후보 메시지에 펀더멘털 상태와 투자 검토 결과를 덧붙입니다.
위험 상태는 투자 의사결정 지원용이며 절대적인 기업 부실 판정이 아닙니다.

## 실행

```powershell
# 전체 시장: 실제 조회, CSV·미리보기 저장, Telegram 전송 없음
.\.venv\Scripts\python.exe daily_runner.py --market all --env-file .env --dry-run

# 소수 종목부터 공식 자료·설정을 점검하는 방법
.\.venv\Scripts\python.exe daily_runner.py --market kr --symbols 033780 --env-file .env --dry-run
.\.venv\Scripts\python.exe daily_runner.py --market us --symbols AAPL --env-file .env --dry-run

# 기존 분석만 실행
.\.venv\Scripts\python.exe daily_runner.py --market all --env-file .env --dry-run --no-fundamentals

# 별도 설정·상태 디렉터리
.\.venv\Scripts\python.exe daily_runner.py --market all --env-file .env --dry-run --fundamental-config fundamental_config.json --state-dir state

# 네트워크·Telegram 없이 계산과 회귀 테스트
.\.venv\Scripts\python.exe -m pytest -q
```

`--dry-run`을 제거하면 기존처럼 실제 Telegram 발송이 활성화됩니다. 개발 검증은 dry-run만
수행합니다. `--env-file` 생략 시 기존 기본 경로인 `../my-stock-agent-starter/.env`를 사용합니다.

미국 공식 API에는 `SEC_USER_AGENT` 환경변수가 필요합니다. 프로젝트/조직 이름과 실제 연락
이메일을 설정합니다. 예시 문자열을 실제 연락처로 바꾸어 사용하세요.
한국은 기존 `OPENDART_API_KEY`를 재사용합니다. 한국 배당 분석에는 기존
`DATA_GO_KR_SERVICE_KEY`도 필요합니다. 키와 인증정보 포함 URL은 재무 캐시에 저장하지 않습니다.

## 출처와 지원 범위

| 대상 | 수집·분류 | 제한 |
| --- | --- | --- |
| 미국 | SEC ticker/CIK, Submissions, Company Facts; 공식 SIC 분류 | 표준 US GAAP 또는 IFRS 전체 기업 계정만 매핑. 기업 확장 태그·차원별 주석은 자동 추정하지 않음 |
| 한국 | OpenDART 법인·기업개황·공시목록·전체 재무제표; KSIC 분류 | 초기 자동 기간 처리는 12월 결산법인. 연결 이력이 있으면 별도 수치로 공백을 채우지 않음 |
| 유럽 | 종목별 검증된 공식 공시를 정규화한 JSON 연결 | 유럽 전체 ESEF 자동 다운로드·해석은 미지원. 미연결 종목은 데이터 부족 |

SEC: https://www.sec.gov/search-filings/edgar-application-programming-interfaces

OpenDART 전체 재무제표: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020

OpenDART 공시검색: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001

KSIC 분류 근거: https://www.kostat.go.kr/boardDownload.es?bid=107&list_no=428660&seq=6

분류는 `GENERAL`, `BANK`, `INSURER`, `REIT`, `OTHER_FINANCIAL`, `UNKNOWN`입니다.
공식 산업 코드로 식별 가능한 경우에만 자동 분류합니다. 부동산 업종 코드처럼 REIT 여부가
불명확한 경우, 금융지주처럼 사업 구조별 검토가 필요한 경우는 종목별 근거를 확인해 덮어쓸 수
있습니다. 과거 분석일보다 나중에 관측·검토한 산업 분류는 과거 분석에 적용하지 않습니다.
종목명과 기존 CSV의 위험 설명만으로 산업을 추정하지 않습니다.

## 설정

`fundamental_config.json`을 복사하여 수정하고 `--fundamental-config`로 지정할 수 있습니다.
전체 설정 형식을 유지해야 합니다. 주요 항목은 다음과 같습니다.

| 설정 | 기본 동작 |
| --- | --- |
| `enabled` | 펀더멘털 분석 사용 |
| `exclude_high_risk` | false: 기존 배당 후보에서 위험 기업을 자동 제외하지 않음 |
| `refresh_days` | 7일: 공시 변경을 탐지하지 못해도 재조회 |
| `history_years` | 최근 6년 및 당해 연도 수집 목표 |
| `min_annual_periods` / `min_quarter_periods` | 연간 5개 / 분기 8개 이상 |
| `max_annual_age_days` / `max_quarter_age_days` | 550일 / 200일 초과 시 자료 지연 |
| `industries` | 산업별 필수 항목과 경고·심각 기준 |
| `single_warning_status` | WARNING: 경고 하나도 정상으로 표시하지 않는 보수적 기본값 |
| `securities` | 종목별 분류 근거·식별자·검증 정보·추가 공시 파일 |

종목별 설정 예시 형식입니다. 아래 `EXAMPLE`은 실제 종목·검토 결과가 아닙니다.

```json
{
  "eu:EXAMPLE": {
    "industry": "INSURER",
    "source": "실제 산업 분류를 검증한 공식 공시 URL",
    "reviewed_on": "2026-10-27",
    "facts_file": "data/verified_example.json"
  }
}
```

이 객체를 전체 설정의 `securities` 안에 넣습니다. 미국은 `cik`, 한국은 `corp_code`로
검증된 식별자를 지정할 수 있습니다. 자동 매핑도 발행사·종목코드 일치를 검증합니다.

EPS의 분기 합산과 주식분할 기준을 확인한 SEC 종목에 한해서 `eps_verified: true`,
`eps_source`(근거 URL), `source`, `reviewed_on`을 지정합니다. 미검증 상태를 해제하기 위한
임의 설정으로 사용하지 마세요. `dividend_split_verified`는 과거 주당배당금의 동일 분할 기준
비교를 확인한 경우에만 사용합니다. 차입금 구성항목이 전체 이자부 차입금을 포함함을 확인한
경우에만 `debt_components_complete: true`를 지정합니다. 존재하지 않는 구성항목은 0으로
채우지 않으므로 이 설정만으로 누락 자료가 보완되지는 않습니다.

## 계산과 판정

수치마다 시작일·종료일·공시일·통화/단위·연결/별도·회계기준·원천 URL·공시 식별자·태그를
저장합니다. 정정공시는 분석 기준일까지 공개된 최신 버전을 선택합니다. 현재 DART API가
정정 후 자료만 제공하는 경우, 과거 분석 시점의 원본을 복원하지 않고 N/A로 처리합니다.

독립 분기는 기간 일수를 확인합니다. 누적 수치는 같은 회계연도 시작일·계정·범위·통화의
직전 누적 수치와 차감하여 계산하며, 연간 수치를 분기로 중복 합산하지 않습니다.
TTM 비교는 연속된 8개 분기와 동일 계정·회계 범위·통화가 확인될 때만 수행합니다.
EPS는 누적 EPS의 차감으로 만들지 않으며 검증된 독립 분기만 합산합니다.
반기만 공시하는 기업을 임의로 분기 기업으로 변환하지 않습니다.

일반기업 기준:

| 지표 | 경고 | 심각 |
| --- | --- | --- |
| 매출 TTM 성장률 | -10% 이하 | -20% 이하 |
| 영업이익·희석 EPS TTM 성장률 | -20% 이하 | -40% 이하 또는 흑자→적자 |
| 연간 OCF·FCF | 최근 1년 음수 | 2년 연속 음수 |
| 순차입금/TTM EBITDA | 3.5배 초과 | 5배 초과 |
| 현금배당 지급액 > 연간 FCF | 경고 | 이 조건 하나로 심각 판정하지 않음 |

기준 이익이 0 이하이면 감소율 대신 적자 지속·흑자 전환을 기록합니다.
순이익 0 이하인 경우 현금전환율·이익 대비 배당성향으로 정상/위험을 판단하지 않습니다.
EBITDA 0 이하이면 부채 배수는 N/A입니다. FCF 음수와 함께 CAPEX 증가 여부를 표시하고
설비투자 확대인지 추가 확인하도록 합니다. 단기·장기 차입금 몇 개만 확보된 상태를 전체
차입금으로 간주하지 않습니다. 보통주 배당만 존재하는 태그도 총 현금배당으로 대체하지 않습니다.

은행·보험·기타 금융에는 일반기업의 FCF·순차입금/EBITDA 규칙을 적용하지 않습니다.
순이익·EPS 변화, 연간 순이익/평균 자기자본 ROE, 지급배당/순이익을 계산하고,
확보된 CET1·NPL·신용손실·보험/투자손익을 별도로 평가합니다.
REIT에는 FFO/AFFO·임대수익 변화, AFFO 배당성향, 부채·이자보상배율을 사용합니다.
금융·REIT 설정의 수치 기준은 초기 분석 정책 예시이며 국가별 법정 최소 기준을 의미하지
않습니다. 보험 지급여력은 국가별 정의·임계값이 달라 기본 위험 임계값을 지정하지 않았습니다.
수치가 있어도 임계값이 미설정이면 미평가입니다. 해당 산업에 맞는 별도 설정 파일을 사용하세요.

필수 지표 누락·기간 불일치·이력 부족·자료 지연이면 `INSUFFICIENT_DATA`가 우선합니다.
이때도 관측된 위험 신호와 근거는 보존합니다. 유효한 분석에서는 심각 2개 이상은
`HIGH_RISK`, 심각 1개 또는 경고 2개 이상은 `WARNING`, 신호가 없으면 `NORMAL`입니다.
요청 규칙에 정의되지 않은 경고 1개는 기본적으로 `WARNING`입니다.

## 추가 공식 자료 연결

EBITDA·총차입금·CET1·NPL·지급여력·FFO/AFFO는 기업마다 태그·정의가 달라 자동 추정하지
않습니다. 미국·한국도 `facts_file`로 검증된 추가 공시를 연결할 수 있습니다. 기존 계정과
충돌하는 동일 기간 값은 오류로 처리합니다. 아래는 **가상 형식 예시**이며 공식 데이터가 아닙니다.

```json
{
  "market": "eu",
  "code": "EXAMPLE",
  "source": "가상 예시: 실제 사용 시 공식 보고서명과 검토 근거로 교체",
  "facts": [{
    "metric": "cet1", "value": 14.2,
    "start": null, "end": "2025-12-31", "filed": "2026-03-20",
    "unit": "percent", "scope": "consolidated", "standard": "ifrs-full",
    "source": "https://example.invalid/illustration-only",
    "accession": "SYNTHETIC-EXAMPLE", "tag": "cet1",
    "eps_verified": false
  }]
}
```

금액은 천원·백만원 같은 배율을 원 통화 금액으로 검증해 정규화한 후 입력합니다.
비율은 `percent`, 주당 수치는 `USD/shares`처럼 입력합니다. 기간 손익은 `start`가 필요하고
재무상태표·자본비율은 `start: null`입니다. 모든 수치는 실제 공개일과 공식 출처가 필요합니다.
현재 파일 경로는 실행 작업 디렉터리를 기준으로 해석합니다.

기본 공식 표준 계정만으로 모든 종목의 필수 자료가 충족되는 것은 아닙니다. 특히 검증된
EPS·EBITDA·총차입금이 없으면 일반기업도 정상 판정을 받을 수 없습니다. 이는 자료 부족을
정상으로 오인하지 않기 위한 동작입니다. 필수 항목을 줄여 결과를 정상으로 만드는 대신
검증된 추가 자료를 연결하는 것이 우선입니다.

## 파일과 알림 이력

| 파일 | 내용 |
| --- | --- |
| `reports/friendly_{market}_fundamental.csv` | 전체 종목 지표·상태·후보·자료 품질, UTF-8 BOM |
| `reports/friendly_{market}_fundamental_errors.csv` | 수집·계산 실패 |
| `reports/friendly_{market}_fundamental.audit.json` | 원시 정규화 수치·분류 근거·계산 입력·미평가 사유 |
| `reports/telegram_preview.txt` | 기존 알림과 신규 위험 알림 미리보기 |
| `state/fundamentals.sqlite3` | 본 실행의 HTTP 캐시·누적 평가·알림 outbox·발송 이력 |
| `state/fundamentals-preview.sqlite3` | dry-run 전용 상태, 본 실행의 최초 분석·발송 상태와 분리 |

주가·배당 후보 재계산은 실행마다 수행합니다. 재무 자료는 저장된 데이터를 재사용하고,
SEC Submissions / DART 공시목록을 최대 하루 한 번 확인합니다. 신규 공시·정정 발견 시
캐시된 재무 자료를 갱신하고, 변경 발견이 없어도 설정 주기마다 재조회합니다.
과거 재무 근거·평가·발송 이력은 보존하며 35일 이상 된 교체 가능한 HTTP 캐시만 정리합니다.

최초 분석(`INITIAL`), 정상→주의/위험·주의→위험(`UPGRADE`), 새 심각 신호(`NEW_SEVERE`)를
구분합니다. 최초 분석은 배당 후보 또는 위험 신호가 있는 종목만 별도 메시지로 만듭니다.
같은 근거 버전의 위험 알림은 중복 발송하지 않습니다. 일시적인 데이터 부족으로 인한 정상화는
위험 해소로 간주하지 않습니다. 발송 성공 전후 상태를 별도 기록하며 네트워크 오류로 결과가
불명확하면 `UNCERTAIN`으로 보관하고 자동 재시도하지 않습니다. 이런 알림의 재전송은
Telegram에서 실제 도착 여부를 확인한 뒤 해당 발송 이력을 명시적으로 정리해야 합니다.

재무 수집 실패는 기존 주가·배당 알림을 중단하지 않으며 별도 오류 CSV와 데이터 부족으로
기록합니다. 기존 runner 종료 코드의 주가·배당 실패 의미는 유지합니다.

## GitHub Actions의 영구 저장

기존 수요일 16시 KST 스케줄과 최근 2회 결과 artifact 정책은 그대로입니다.
artifact 만료·캐시 소실에 의존하지 않도록 버전 관리되는 S3에 SQLite를 복원·백업하는
`fundamental_state_sync.py`와 선택형 워크플로 단계를 추가했습니다. 이 개발 작업에서는
버킷 생성·원격 쓰기·워크플로 실행·GitHub push를 하지 않습니다.

운영 적용 시 버전 관리가 활성화된 버킷을 별도로 준비하고 다음을 설정합니다.

- Repository Variables: `FUNDAMENTAL_STATE_BUCKET`, `FUNDAMENTAL_STATE_AWS_REGION`.
- 선택 Variables: `FUNDAMENTAL_STATE_KEY`(기본 `dividend/fundamentals.sqlite3`).
- Secrets: `FUNDAMENTAL_STATE_AWS_ACCESS_KEY_ID`, `FUNDAMENTAL_STATE_AWS_SECRET_ACCESS_KEY`.
- 쓰기를 승인한 뒤에만 `FUNDAMENTAL_STATE_WRITE_APPROVED=true`를 설정합니다.
- 최초 객체 생성 시에만 `FUNDAMENTAL_STATE_INITIALIZE=true`를 설정하고 성공 후 제거합니다.
- SEC 실수집에는 Repository Secret `SEC_USER_AGENT`도 설정합니다.

필요 권한은 버킷 버전 관리 조회·객체 목록·지정 객체 읽기/쓰기입니다. 만료 정책은 장기
평가 이력 보존 요구에 맞춰 별도로 정해야 합니다. 버전 관리 자체가 삭제 방지 보장은 아닙니다.
동일 객체를 여러 저장소·로컬 프로세스에서 동시에 쓰지 마세요. 현재 워크플로는 기존
concurrency 그룹으로 단일 실행을 유지합니다.

복원 실패·손상·스키마 불일치는 빈 상태로 바꾸지 않고 중단합니다. 저장소 미설정 시 기존
분석은 실행되지만 Actions 실행 간 재무 캐시·평가 이력·알림 중복 방지는 보장되지 않으며
워크플로에 경고를 남깁니다. 운영 적용 전에 영구 저장 설정이 필요합니다.

## 검증과 미지원

2026-10-27 `python -m pytest -q`: **146개 통과**(기존 회귀 테스트 93개 포함).
자동 테스트는 성장률·누적 분기·TTM·적자 전환·OCF/FCF 지속 음수·EBITDA 0 이하·누락 자료·
산업별 분리·분류 근거 시점·미래 공시 차단·정정 선택·통화/범위 불일치·CSV BOM·상태 상승·
발송 중복/불확실성·S3 복원 보호·기존 주가/배당 회귀를 검증합니다.

2026-10-27 KT&G(033780) 실제 dry-run에서 공식 재무 수치 274개를 수집했습니다.
공시 기준일과 누적 분기·TTM 산식은 `reports/fundamental_live_validation/`에 검증 자료를
보관합니다. 2026년 상반기 말 TTM 매출은 6,945,851,000,000 KRW, 전년 동기 TTM 대비
성장률은 약 +11.46%이며, 독립 산식(직전 연간 + 당해 상반기 - 전년 상반기)과 분기 합산이
일치했습니다. 최신 수집 공시일은 2026-08-14입니다. `verified_calculations.json`은 이 수집
스냅샷의 계산 검증이며 기업 전체의 재무 정상 판정을 의미하지 않습니다.
검증된 추가 EPS·차입금/EBITDA 없이 정상 판정을 하지 않습니다.
미국 실수집은 SEC 연락처 설정이 필요하며, 유럽 자동 ESEF·금융 주석 자동 파싱은 미지원입니다.
한국 비12월 결산·미국 ADR 주당 보고 단위 자동 검증·과거에 수집하지 못한 DART 정정 전 원문
복원도 미지원입니다. 미지원·미검증 자료는 N/A이며 공식 자료로 위장한 추정치는 사용하지 않습니다.

## 구현 파일

- 기존 수정: `daily_runner.py`, `dividend_report.py`, `.github/workflows/daily-stocks.yml`,
  `.env.example`, `.gitignore`, `README.md`, `test_dividend.py`.
- 설정·문서·검사: `fundamental_config.json`, `FUNDAMENTALS.md`, `pytest.ini`, `test_fundamentals.py`.
- 영구 저장 연동: `fundamental_state_sync.py`.
- 데이터 모델·설정·분류: `fundamentals/models.py`, `config.py`, `classification.py`.
- 계산·평가: `fundamentals/periods.py`, `metrics.py`, `evaluation.py`.
- 수집·저장·리포트: `fundamentals/http.py`, `storage.py`, `report.py`.
- Adapter: `fundamentals/adapters/base.py`, `sec.py`, `dart.py`, `europe.py`, `official_file.py`와 패키지 초기화 파일.

`stock_52week.py`, `dividend_metrics.py`, `dividend_data.py`, `kr_dividend_data.py`,
기존 종목 CSV와 배당 설정은 변경하지 않았습니다. 테스트용 YAML 검사 도구와 실제 조회 결과는
무시 대상인 `reports/`에만 저장했습니다. `pytest.ini`는 접근 제한이 있는 기존 `tmp*` 폴더와
생성 자료를 테스트 수집에서 제외합니다.
