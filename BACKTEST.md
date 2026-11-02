# 투자 신호 백테스트

`backtest_runner.py`는 기존 `daily_runner.py`와 분리된 로컬 연구 도구입니다.
기존 주가·배당·펀더멘털·점수 분석과 수요일 GitHub Actions 스케줄은 변경하지 않았습니다.
Telegram 호출 기능은 없으며 요약 텍스트만 저장합니다.

이번 변경 파일은 다음과 같습니다.

- 신규 실행·설정: `backtest_runner.py`, `backtest_config.json`, `examples/backtest_collect.json`
- 신규 모듈: `backtest/__init__.py`, `config.py`, `data.py`, `collection.py`, `signals.py`,
  `events.py`, `portfolio.py`, `metrics.py`, `report.py`, `demo.py` (모두 `backtest/` 아래)
- 신규 테스트: `test_backtest.py`, `test_backtest_integration.py`
- 문서: `BACKTEST.md` 신규, `README.md` 수정

## VS Code 터미널 실행

프로젝트 폴더 `D:\python\dividened`에서 실행합니다. 추가 패키지는 필요 없습니다.

```powershell
# 외부 API 없이 가상 데이터로 두 모드 검증 (실제 성과 아님)
.\.venv\Scripts\python.exe backtest_runner.py --demo --mode both

# 검증된 과거 데이터 파일을 준비한 후 이벤트 연구
.\.venv\Scripts\python.exe backtest_runner.py --config backtest_config.json --data-file state/backtest/market_data.json --mode event --offline

# 독립된 자금·보유자산 회계
.\.venv\Scripts\python.exe backtest_runner.py --config backtest_config.json --data-file state/backtest/market_data.json --mode portfolio --offline

# 한국·미국·유럽 3종목 공개 OHLC 표본 수집 및 입력 검증
.\.venv\Scripts\python.exe backtest_runner.py --config examples/backtest_collect.json --collect

# 수집한 표본 재사용 (네트워크 호출 없음)
.\.venv\Scripts\python.exe backtest_runner.py --config examples/backtest_collect.json --offline

# 전체 회귀 테스트
.\.venv\Scripts\python.exe -m pytest -q
```

설정 형식은 기존 프로젝트와 같은 **JSON**입니다. YAML은 지원하지 않습니다.
`--collect`를 명시하지 않으면 외부 API를 호출하지 않습니다. 입력이 없으면 오류로 종료하며
현재 분석 CSV의 점수·등급을 과거 데이터로 대체하지 않습니다.
`--output-dir 경로`로 저장 위치를 바꿀 수 있습니다. 상대 경로는 실행 작업 폴더 기준입니다.

## 실제 구현을 확인한 내용

기존 `dividend_metrics.calculate/is_candidate`, `fundamentals.evaluation.evaluate`,
`valuation.history.analyze`, `investment.growth/price_risk/scoring`을 재사용합니다.
배당 모드는 기존 설정의 `all`(정기·특별 포함), 현재 TTM 수익률 3% 이상,
완료 월말 36개월 평균 대비 1.2배 이상입니다. 설정 변경은 실행별 `policies.json`에 기록합니다.
점수의 데이터 충족률, 금융업 구분과 HIGH_RISK 59점 상한도 기존 정책 그대로 적용합니다.

기존 52주 고점은 Naver/Yahoo 공급자의 스냅샷 필드였습니다. 백테스트 기본값은
최근 **252개 거래 세션의 분할 조정 일별 고가** 최댓값이며, `high_basis: "close"`로 종가 기준을
명시적으로 선택할 수 있습니다. 두 값이 기존 공급자 고점과 동일하다고 가정하지 않습니다.

기존 SQLite에는 재무 공시일과 버전, 현재 평가 이력이 있으나 정확한 과거 공개 시각·당시 등급·
종목 편입 이력·시가·일별 고가·배당 지급일까지 보장하지 않습니다. 이 때문에 SQLite 전체를
자동으로 엄밀한 과거 입력으로 승격시키지 않습니다. 검증된 사실만 아래 입력 형식으로 가져옵니다.

## 전략 및 비교 표본

요청서에는 “다음 전략” 이후 구체 목록이 없었으므로 아래 비교군을 초기 가설로 설정했습니다.

| ID | 조건 |
|---|---|
| price | 고점 대비 -30% 이하 |
| dividend | price + 기존 배당 저평가 조건 |
| quality | dividend + NORMAL 또는 WARNING |
| value | quality + 기존 역사적 밸류에이션 저평가 |
| score | dividend + 기존 종합 투자 점수 70 이상 |

`strategies`에서 비교군과 기준을 바꿀 수 있습니다. 조건의 모든 입력이 있어야 평가하며,
앞선 조건이 거짓이어도 나머지 자료가 없으면 해당 관측은 평가 불가입니다.
`all`은 각 전략의 유효 자료, `common`은 **동일 종목·동일 날짜에서 설정된 모든 전략의 입력이 있는 경우**입니다.
공통 표본이 비면 별도 UNAVAILABLE 행을 출력합니다. 비교 가능한 전략만 설정하면 해당 비교군의 공통 표본을 얻습니다.
신호는 각 전략의 전체 유효 시계열에서 판정한 후 공통 날짜로 제한하며, 결측 이후 충족 상태가 이어졌다는 이유로 새 신호를 만들지 않습니다.

기본 `universe_mode: current_watchlist`는 입력으로 제공한 현재 관심종목 표본의 과거 연구이며
생존편향을 보고서 제목에 표시합니다. 당시 실제 종목 집합을 복원하지 못합니다.
`historical`은 공개 시각·버전이 검증된 `membership` 이력을 요구합니다. 현재 등급은 어떤 모드에서도
과거 점수에 대입하지 않습니다. 상장폐지 종목도 입력에서 보존해야 하며, 인수·합병·상장폐지 최종 정산을
자동 처리하지 못하는 경우 체결/청산 불가 또는 미해결로 남습니다.

## 시간·체결 규칙

- 신호는 각 거래소의 종가 확정 시각 기준. 그 이후 공개된 공시는 다음 거래일부터 반영합니다.
- 공개 **날짜만** 있으면 다음 거래 세션부터 반영합니다. 회계 결산일은 공개일이 아닙니다.
- `original_version_verified` 없는 재무 수치는 사용하지 않습니다. 정정 전 시점에는 정정 후 수치를 쓰지 않습니다.
- 각 재무 버전의 사용 가능 날짜를 계산하여 기존 재무·밸류에이션 엔진의 월말 시점 필터에도 전달합니다.
- 배당 신호에는 배당락일 이전에 공개된 검증 자료만 사용합니다. 뒤늦게 확인된 배당 수정값으로
  과거 평균을 재작성하지 않으며, 그런 자료만 있으면 배당 전략은 N/A입니다.
- 최초 유효 관측부터 충족하면 `initial=true`. 이후 거짓→참 전환만 신호입니다. 결측은 거짓이 아닙니다.
- 매수는 다음 거래 세션의 **시가**. 그 시가가 없으면 해당 진입을 취소하고 이유를 저장합니다.
- 매수일에서 달력 3·6·12개월을 더합니다. 없는 월말 날짜는 해당 월 마지막 날로 맞춥니다.
  목표일 이후 첫 세션 시가에 매도합니다. 거래정지·시가 누락이면 세션별 지연 사유를 기록합니다.
- 기본적으로 종목·전략·기간별 평가 구간 중복을 제외합니다. `allow_overlap=true`는 이벤트 연구에만
  중복을 허용합니다. 포트폴리오는 항상 동일 종목의 중복 보유를 금지합니다.
- 학습·검증·최종 구간 경계를 넘는 거래는 INCOMPLETE이며 완료 성과에 합산하지 않습니다.
  각 구간은 독립된 시작 자금과 상태를 사용합니다. 경계마다 초기 신호임을 표시합니다.

분할/병합은 효력일 개장 전 주식 수에 적용합니다. 배당락일 개장 전에 보유했던 수량에 권리가 생깁니다.
따라서 배당락일 시가 매수자는 제외하고 시가 매도자는 포함합니다. 지급 전에는 미수배당이며,
시각 없는 지급일은 모든 당일 주문 이후에 현금 처리합니다. 휴일 지급은 다음 평가일에 반영하되
환율은 실제 지급일의 허용 가능한 과거 환율을 사용합니다.

## 자금·수익률·환율

포트폴리오는 기본 1억원, 최대 10종목, 매수 직전 순자산의 10%를 목표로 합니다.
수수료까지 포함한 목표 금액의 현금이 부족하면 축소 매수하지 않고 건너뜁니다. 기본 정수 주식이며
`fractional_shares=true`로 소수점 매매 가정을 선택할 수 있습니다.
거래소 개장 시각 순서로 처리하고 같은 시각은 고정 종목 ID 순서입니다. 다른 시장의 아직 열리지 않은
시가·나중 종가를 매수 자금 계산에 쓰지 않습니다. 시장별 조기 폐장 등은 `session_times`로 제공해야 합니다.
차입은 없으며 배당 현금은 별도 보관하여 재투자하지 않습니다.

이벤트는 가격수익률, 총배당 포함 수익률, 비용·원천징수 차감 순수익률을 별도로 기록합니다.
이벤트의 총수익에는 청산일까지 생긴 배당 권리가 포함되며 지급일 이후 현금화와 구분됩니다.
원화 순수익은 매수·매도·배당 지급일별 환율이 있어야 계산하고, 평가 종료일까지 미지급이면 N/A입니다.
순수익률 통계 분모는 완료되고 순수익을 계산할 수 있는 거래만 사용합니다.
이벤트 MDD는 원금·일별 평가액·배당 권리를 포함한 비용 차감 전 자산가치의 이전 최고점 기준입니다.
이벤트 수익률을 연결한 CAGR는 만들지 않습니다.

전략 데이터나 체결 자료가 없을 때 전액 현금 0%를 전략 성과로 보고하지 않습니다.
포트폴리오는 현금+주식 평가액+미수배당의 일별 순자산이 모두 있어야 CAGR·Sharpe·MDD를 제공합니다.
미청산 주식도 평가합니다. 기존 보유종목의 해당 거래일 종가가 없으면 직전 값을 무조건 이월하지 않습니다.
CAGR는 실제 경과일/365.25, 변동성과 Sharpe는 설정상 252개 평가 세션으로 연율화하며
무위험수익률 기본 0은 가정입니다. 회전율은 양방향 거래금액 합계/평균 순자산입니다.

비용은 `costs`의 비율 단위(0.001=0.1%) 가정이며 특정 국가의 실제 과거 세율이라고 주장하지 않습니다.
현지통화는 이벤트별로 구분하고 글로벌 포트폴리오는 KRW만 지원합니다. FX 데이터 방향은
**현지통화 1단위당 KRW**입니다. 과거 값만 최대 `fx_max_age_days`까지 사용합니다.
체결·평가 시각 이후 환율은 배제하며, 환율의 공개 시각이 없으면 다음 UTC 날짜부터 사용할 수 있습니다.
환율 없이 USD·EUR·KRW 금액을 직접 합산하지 않습니다.

벤치마크는 검증된 시리즈를 `benchmarks`에 넣고 시장별 ID를 설정해야 합니다. 기본 ID는 null이며
KOSPI·S&P 500·STOXX 600을 확보한 것처럼 가정하지 않습니다. 종목 진입·청산 날짜의 시가와
현지통화가 일치해야 합니다. `basis=price`이면 가격수익과, `total_return`이면 배당 포함 수익과 비교합니다.
글로벌 포트폴리오의 국가별 벤치마크 배분은 아직 구현하지 않아 그 초과성과는 N/A입니다.

## 입력 JSON 계약

루트는 `schema_version: 1`, `securities: [...]`, `fx: {...}`, `benchmarks: {...}`입니다.
스키마와 완전한 가상 예시는 `backtest/data.py`, `backtest/demo.py`에 있습니다.
검증되지 않은 자료에 `verified=true`를 붙이면 시간 검증을 통과시킬 수 있으므로 반드시 실제 출처를 검토해야 합니다.

종목별 필수 항목:

- `id`, `name`, `market`(kr/us/eu), `currency`(KRW/USD/EUR/GBP 등 본위 단위)
- `timezone`(IANA), `open_time`, `close_time`; 선택 `session_times[날짜] = {open,close}`
- `source`, `version`, 시간대가 붙은 `retrieved_at`
- `price_basis: "raw"`, `bars: [{date,open,high,close,tradable?,status?}]`
- `sessions`: 독립 검증된 거래소 거래일 전체. 정지된 개별 종목의 거래일을 달력에서 지우지 않습니다.
- `calendar_verified`, 선택 `calendar_through`(달력 검증 범위 종료일)
- `splits: [{date,ratio}]`(2:1 분할=2), `actions_coverage: {verified,source,from,through}`
- `dividends: [{id,date,pay_date,amount,currency,kind,...공개근거}]`, `dividends_coverage` 동일 형식

배당금·주가는 당시 원주식/본위통화 기준입니다. 총수익 수정주가는 실행 입력으로 거부합니다.
동일 배당 권리를 중복 입력할 수 없습니다. 무배당 기업도 검증된 빈 배당 이력 범위가 있어야
무배당으로 취급하며, 이력 없는 것을 배당 0으로 처리하지 않습니다.

재무·등급·분류의 공통 **공개근거**:

```json
{
  "period_end": "2020-12-31",
  "published_at": "2021-02-15T17:30:00+09:00",
  "retrieved_at": "2026-11-02T12:00:00+00:00",
  "source": "검증한 원문 공시 URL",
  "version": "원공시 식별자",
  "original_version_verified": true
}
```

`published_at` 대신 `published_date`를 사용할 수 있습니다. `available_at`은 사용 가능 시각을
더 늦출 때만 적용됩니다. 수집 시각은 감사 정보이며 공개 시각을 대체하지 않습니다.

- `facts`: 공개근거 + `fact`(기존 `fundamentals.models.Fact`의 전체 필드). 원공시와 정정공시를 각각 보존합니다.
- `classifications`: 공개근거 + `industry`(GENERAL/BANK/INSURER/REIT/OTHER_FINANCIAL/UNKNOWN).
- `grades`: 공개근거 + `grade`(기존 주주친화 등급). 현재 CSV 등급은 가져오지 않습니다.
- `reviews`: 공개근거 + `value`(기존 회계·EPS·주식수·EV 검토 설정).
- `membership`: 공개근거 + `member: true/false`; historical 모드에서 사용합니다.
- `fx[통화]`: `[{date,krw_per_unit,available_at?}]`.
- `benchmarks[ID]`: `{currency,basis,source,bars:[{date,open,close?}]}`.

## 출력과 재현

매 실행 `reports/backtests/UTC시각_고유ID/`가 생깁니다.

| 파일 | 내용 |
|---|---|
| report.html | 전략 평가 가능 여부, 전체/공통 표본, 이벤트·포트폴리오 결과 |
| summary.json | 데이터 출처·버전, 제외 이유, 표본 수, 평균·중앙값·분위·승률·초과성과 |
| signals.csv | 모든 평가일의 입력, 참/거짓/결측 및 근거 |
| events.csv | 신호·기간별 실제 진입·청산·미완료·중복 제외·비용·배당·수익률 |
| comparison.csv | 기간·국가·신호 연도·전략·표본별 비교 |
| strategy_support.csv | 평가 가능 관측 수와 데이터 충족률 |
| portfolio_NNN_ledger.csv / nav.csv | 거래·배당 원장, 일별 현금·미수배당·평가자산 |
| input.json / config.json / policies.json | 실행 데이터 스냅샷, 백테스트 설정, 기존 계산 정책 |
| telegram_preview.txt | 로컬 요약 미리보기. 발송 없음 |

CSV는 UTF-8 BOM입니다. 입력과 전체 계산 코드의 SHA-256을 저장하며 Git이 설치돼 있으면 커밋도 저장합니다.
캐시는 `state/backtest/`에 있으며 실패 응답으로 정상 입력 파일을 덮어쓰지 않습니다. 캐시·보고서는 기존
`.gitignore`의 state/reports 제외 정책을 따릅니다. GitHub Actions에 새 스케줄은 추가하지 않았습니다.

민감도 비교는 `sensitivity.drawdown_pct: [-20,-30,-40]`, `score_min: [70,85]`처럼 설정합니다.
기본 설정과 각 단일 변경 실험을 모두 별도 폴더로 저장하며 전수 조합 최적화는 하지 않습니다.
학습·검증·최종 구간은 `segments`에서 시간순으로 지정합니다. 자동 파라미터 선택·근사 공시 지연 모드·
손절/익절/점수 청산·배당 재투자는 미지원이며 관련 옵션을 켜면 명시적으로 거부합니다.

## 검증 결과와 남은 범위

신규 고정 데이터 테스트는 다음 거래일 시가, 3/6/12개월·윤년·휴장, 공개시각·정정공시,
결측 상태와 신호 중복, 분할·배당 권리/지급, 수수료·환율 방향·시각, MDD·벤치마크·승률,
자금 부족·일별 회계 합계, 자료 없는 전략의 0% 성과 방지, 오프라인 CLI와 BOM 저장을 검증합니다.
기존 테스트 189개와 신규 33개를 합한 **222개 테스트**를 실행합니다. 가상 데모는 가격·배당 전략이 평가 가능하고, 과거 재무·등급을
가공하지 않으므로 재무·밸류에이션·점수 비교군은 평가 불가입니다.

2026-11-02에 KT&G(033780.KS), Apple(AAPL), Allianz(ALV.DE)의 Yahoo OHLC와 분할 자료를
읽기 전용으로 조회했습니다. 2025-01-01~2026-10-30에 가격 평가 1,369개 중 1,117개가 가능했고
252개는 고가 이력 누락으로 제외했습니다. 가격 조건 신호 1개가 있었으나 독립 검증 거래일 달력이 없어
실제 진입은 0개이며 **실제 투자 성과는 산출하지 않았습니다**. 배당 공개·지급 시각, 과거 재무 버전,
등급·편입 이력, 환율 및 벤치마크 전체 시계열은 확보하지 못했습니다.

수집기의 주가 날짜는 거래소 달력을 대체하지 않으며 임시 개·폐장 시각도 검증된 것으로 취급하지 않습니다.
이 자료는 가격 입력·결측 방어 검증까지만 의미합니다. 모든 등록 종목의 장기 실전 성과 검증을 완료했다고
해석하면 안 됩니다. 현재 코드에서 과거 회계근거를 검증해 가져오면 추가 전략을 계산할 수 있지만,
국가별 공식 과거 자료의 완전한 자동 수집·벤치마크·상장폐지 정산 자료 확보는 남아 있습니다.
