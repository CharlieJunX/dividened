# 한국·미국·유럽 주식 52주 최고가 대비 등락률

자동 조회 및 Telegram 알림:

```powershell
python -m pip install -r requirements.txt
python daily_runner.py --market all
```

기본적으로 `../my-stock-agent-starter/.env`에서 `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`를 읽습니다. 이미 설정된 환경변수는 덮어쓰지 않습니다.
`--env-file 경로`로 변경하거나 `--dry-run`으로 전송 없이 조회할 수 있습니다.
등락률이 -30.00% 이하인 종목의 symbol, 회사명, 등락률을 하락 순으로 전송합니다.
해당 종목이 없으면 메시지를 보내지 않습니다. 결과는 `reports/`에 저장합니다.

GitHub Actions는 매일 07:00 UTC(한국시간 오후 4시)에 실행합니다.
GitHub 예약 실행은 지연될 수 있습니다. 저장소 Settings → Secrets and variables →
Actions에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`를 등록해야 발송됩니다.
로컬 `.env` 파일은 업로드하지 않습니다. Actions 탭에서 수동 실행도 가능합니다.
실행 결과 CSV와 가격 기준 시각은 Actions의 `stock-results-*` 아티팩트에서 확인합니다.

시장 선택:

```powershell
python stock_52week.py kr
python stock_52week.py us
python stock_52week.py eu
python stock_52week.py all
```

`--market us` 형식도 지원합니다. 생략하면 `kr`입니다.
`all`은 한국 → 미국 → 유럽 순서로 실행하며 시장별 파일을 각각 저장합니다.

| 옵션 | 입력 파일 | 결과 파일 |
| --- | --- | --- |
| kr | friendly_kr.csv | friendly_kr_52week.csv |
| us | friendly_us.csv | friendly_us_52week.csv |
| eu | friendly_eu.csv | friendly_eu_52week.csv |

입력 기본 형식은 `종목코드,회사명,등급,약점(리스크)`입니다.
출력은 기존 4개 열에 등락률을 추가한 5개 열입니다.
리스크 내용은 쉼표·줄바꿈을 포함해 그대로 보존합니다. 쉼표나 줄바꿈이 포함된 값은
CSV 규칙에 따라 큰따옴표로 감싸세요.
이전 `종목코드,등급` 및 `종목코드,회사명,등급` 형식도 지원하며 리스크는 공란으로 저장합니다.
회사명이 있으면 입력한 이름을 유지하고 없으면 제공업체의 이름을 사용합니다.
`Symbol`, `Ticker`, `Code`, `종목코드` 헤더는 파일 중간에 반복되어도 건너뜁니다.
유럽 종목은 `MUV2.DE`, `TTE.PA`, `ASML.AS`처럼 Yahoo Finance 거래소 접미사가 필요합니다.
미국도 Yahoo Finance 종목코드를 사용하며 다른 종목으로 자동 대체하지 않습니다.

해외 시세는 Yahoo Finance chart API의 `regularMarketPrice`, `fiftyTwoWeekHigh`를 사용합니다.
현재가는 정규장 기준 제공 가격이며 휴장 중에는 최근 정규장 거래 가격입니다.
시세 지연이 있을 수 있고 52주 최고가의 기간·수정주가 기준은 제공업체를 따릅니다.
동일 통화의 가격끼리 계산하므로 환율 변환은 필요하지 않습니다.
공개 비공식 API이므로 서비스 변경이나 호출 제한에 따라 실패할 수 있습니다.

시장별 `_errors.csv`, `.audit.json`도 생성합니다. 실패 종목은 회사명과 등급을 유지하고
등락률을 공란으로 남깁니다. 실패가 있어도 다음 시장을 처리하며 종료코드는 1입니다.
입력 오류는 모든 시장의 조회 시작 전에 검사하여 종료코드 2로 종료합니다.
감사 JSON에는 성공 종목의 계산 가격, 시각, 출처, 통화를 저장합니다.

단일 시장에서 경로를 지정하는 예:

```powershell
python stock_52week.py us --input friendly_us.csv --output result_us.csv
```

`all`에서는 `--input`과 `--output`을 사용할 수 없습니다.
기본 파일 경로는 프로그램이 있는 폴더 기준입니다.

Python 3.10 이상. 별도 패키지나 API 키 없이 실행합니다.

```powershell
python stock_52week.py
```

한국 입력에서는 첫 번째 열의 6자리 종목코드를 읽어 앞자리 0을 유지합니다.
모든 시장에서 등락률의 숫자 오름차순으로 저장하여 가장 많이 하락한 종목이 상단에 옵니다.
등락률 공란인 조회 실패 종목은 맨 아래에 두며, 같은 등락률끼리는 입력 순서를 유지합니다.
헤더 없는 파일과 헤더 있는 파일을 모두 지원합니다.

입력 예시 (헤더 생략 가능):

```csv
종목코드,등급
138040,A
033780,A
138930,B
```

등급은 입력한 문자열을 그대로 전달하며 앞뒤 공백만 제거합니다.
등급이 없거나 기존 한 열 형식이면 공란으로 처리합니다. 입력이 네 열을 초과하면 오류를 표시합니다.

출력 열: `종목코드,회사명,등급,약점(리스크),최근52주 최고가대비 현재가 등락율(%)`

계산식: `(현재가 / 52주 최고가 - 1) * 100`. 소수점 둘째 자리까지 반올림합니다.
최고가보다 20% 낮으면 `-20.00`입니다.

네이버 금융 공개 JSON 응답의 `basic.closePrice`와
`integration.totalInfos.highPriceOf52Weeks`를 사용합니다.
52주 최고가는 제공업체의 기준을 따릅니다. 현재가는 조회 시점의 제공 가격으로,
휴장 시 최근 거래 가격이며 실시간 체결가를 보장하지 않습니다.
장중에는 두 요청 사이에 가격이 바뀔 수 있습니다. 비공식 API이므로 서비스 변경 시 수정이 필요합니다.

오류 종목도 결과에 유지하며 등락률은 공란으로 남깁니다.
`friendly_kr_52week_errors.csv`에 오류를 기록하고 실패가 있으면 종료코드 1을 반환합니다.
`friendly_kr_52week.audit.json`에는 계산에 사용한 가격과 시각을 저장합니다.

```powershell
python stock_52week.py --input friendly_kr.csv --output result.csv
```

CSV는 UTF-8 BOM으로 저장합니다. 파일 안의 종목코드는 앞자리 0을 유지합니다.
Excel에서 열 때에는 데이터 가져오기에서 종목코드 열을 텍스트로 지정하세요.

## 배당 저평가 탐지

기존 52주 하락 알림에 배당 후보 리포트를 추가합니다. 기존 CSV와 `stock_52week.py`
단독 실행 방식은 유지합니다. 한국 배당은 금융위원회·한국예탁결제원 자료를 수집하고
OpenDART 정기보고서로 사업연도별 연간 지표를 계산합니다. 주가 이력과 미국·유럽 배당은 기존
Yahoo chart를 사용합니다. 추가 Python 패키지는 필요 없지만 한국 배당에는 아래 두 API 키가 필요합니다.

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
python daily_runner.py --market all --dry-run
```

dry-run은 실제 이력을 조회하고 CSV·메시지 본문을 저장·출력하며 Telegram은 호출하지
않습니다. 한국 배당 API 키는 dry-run에도 필요합니다. Telegram 인증정보는 실제 발송에만 필요합니다.
실제 발송 시 `--dry-run`을 빼고 기존
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `--env-file`을 사용합니다.

### 한국 배당 API 설정

1. [금융위원회 주식배당정보](https://www.data.go.kr/data/15043284/openapi.do)를 활용신청하고
   인증키를 `DATA_GO_KR_SERVICE_KEY`에 설정합니다. Decoding 키를 권장하며 Encoding 키도 지원합니다.
2. [OpenDART](https://opendart.fss.or.kr/)에서 발급받은 인증키를 `OPENDART_API_KEY`에 설정합니다.
3. 로컬에서는 `.env.example`을 참고해 `.env`를 작성하고 `--env-file .env`로 지정합니다.
   기본 env 경로는 기존대로 `../my-stock-agent-starter/.env`입니다.
   GitHub Actions에서는 같은 이름의 두 Repository Secrets를 등록합니다.

```powershell
python daily_runner.py --market kr --symbols 138040 --env-file .env --dry-run
```

금융위원회 **V2** `GetStocDiviInfoService_V2/getDiviInfo_V2`를 사용합니다.
종목코드를 DART 법인코드 및 기업개황의 법인등록번호로 연결하고, 응답의 ISIN에 포함된
6자리 종목코드까지 일치하는 자료만 선택합니다. 이름이 비슷한 회사나 보통주·우선주로
자동 대체하지 않습니다. 모든 페이지를 조회하고 동일 배당기준일의 반복 스냅샷은
분석 기준일까지의 최신 자료를 선택합니다. 같은 스냅샷의 충돌은 확인을 요구합니다.
차등배당이 있는 경우에도 일반주주 기준인 `stckGenrDvdnAmt`를 사용합니다.
차등배당금(`stckGrdnDvdnAmt`)을 일반 배당금에 더하거나 빼지 않습니다.
이벤트의 `shareholder_basis`·`amount_field`에 선택 기준을, `raw_differential_amount`와
`raw`에 원래 차등배당 금액·원본을 보존합니다. 보고서에는 일반주주 기준과 차등배당 적용 경고를 표시합니다.

**한국은 배당기준일 기준**, 미국·유럽은 **배당락일 기준**으로 TTM과 월별 수익률을 계산합니다.
국내 원본에 없는 배당락일을 임의 생성하지 않습니다. CSV·Telegram의 `배당 계산 기준`에
날짜 기준을 표시하고 감사 JSON에는 `record_date`, `payment_date`, `snapshot_date`를 보존합니다.
따라서 기존 Yahoo 결과와 날짜 경계 근처의 합계가 달라질 수 있습니다.

OpenDART `alotMatter`의 사업보고서에서 최근 완료된 6개 사업연도 주당현금배당금을 읽어
**연간 배당금·5년 CAGR·연간 배당 삭감**에 사용합니다. **TTM·현재 수익률·3년/5년 평균·배율**은
예탁결제원 배당기준일 이벤트를 그대로 사용합니다. 두 지표군은 독립적으로 계산합니다.
국내 `배당 삭감 여부`는 DART 사업연도별 감소만 의미하며 TTM 감소를 합치지 않습니다.
CSV·Telegram의 `배당 계산 기준`에도 두 기준을 함께 표시합니다.

같은 달력연도의 배당기준일 합계와 DART 사업연도 합계는 참고용으로 비교합니다.
**합계 차이만으로 오류·삭감·누락을 판정하거나 지표를 N/A로 만들지 않습니다.**
예를 들어 KT&G는 2024 사업연도 배당금 5,400원을 연간 지표에 사용하고,
2025-02-28 기준 4,200원 이벤트는 날짜를 바꾸지 않아 해당 날짜의 TTM에 포함합니다.
전년도 귀속으로 임의 이동하지 않으며 개별 이벤트의 사업연도는 공시 근거 없이 확정하지 않습니다.
금액 일치도 귀속연도나 이벤트 이력의 완전성을 입증하지는 않습니다.
연도별 참고 비교는 `annual_cross_checks`, 공시 원문과 근거는 `dart_annual`,
분석 결과의 사업연도 합계는 `annual_dividends`, 기준일 달력연도 합계는
`record_year_dividends`, 연간 계산 기준은 `annual_dividend_basis`에 기록합니다.

DART의 연간 금액이 누락되거나 `-`이면 해당 연도는 N/A이고, 6개 연도가 모두 확인되지 않으면
5년 CAGR·연간 삭감을 보류합니다. 연간 API 조회 실패도 경고로 기록하고 이벤트 수익률 계산을 계속합니다.
단, 종목 식별에 필요한 법인코드·기업개황 실패는 계속 조회 실패입니다.
분할·병합이 계산 대상 기간에 있으면 DART 원시 연간 금액은 보존하되 성장률·연간 삭감을 보류합니다.
KSD 원시 이벤트 배당금만 이후 분할 비율로 조정해 이미 분할 조정된 Yahoo 종가와 맞춥니다.
원래 이벤트 배당금은 `raw_amount`에 보존합니다.

키 누락·인증 오류·배당 이력 없음·페이지 누락은 `조회 실패` 및 N/A로 기록합니다.
Yahoo 배당으로 자동 복귀하지 않습니다. 최근 12개월 국내 배당 기록이 없는 경우도
실제 무배당으로 확정하지 않고 TTM·현재 수익률을 N/A로 표시합니다.
이때도 계산 가능한 과거 평균과 DART 연간 지표는 유지합니다.
OpenDART 미조회 및 `-`도 0원으로 바꾸지 않습니다. API 키는 로그·감사 URL에 저장하지 않습니다.
로그와 CSV에는 정기보고서 없음, 주식종류 미표기, 배당금 `-` 공시 등을 각각 구분해 표시합니다.
주식종류가 빠진 연도는 조회한 후속 사업보고서의 **동일 주식종류** 전기(`frmtrm`)·전전기(`lwfr`)
금액으로 확인할 수 있을 때만 보완합니다. 분석 기준일 이후 공시는 사용하지 않으며,
해당 사업연도와 후속 공시 연도·접수번호·사용 열·원래 미확인 행을 감사 JSON에 보존합니다.
예를 들어 2025 사업보고서의 보통주 전기 금액은 2024년에만 사용하고 2025년 당기 `-`를 대체하지 않습니다.
이렇게 금액을 확인해도 배당기준일 연도와 사업연도의 차이는 참고 정보로 남깁니다.

현재 자동 법인 매핑은 DART에 종목코드가 등록된 주식, 연간 대조는 12월 결산법인이 대상입니다.
우선주처럼 별도 매핑이 필요하면 공식 법인코드와 주식종류를 명시합니다.
매핑 구조 예시는 다음과 같으며, 실제 코드와 법인코드를 확인해 입력해야 합니다.

```json
{"korean_dividends": {"securities": {
  "123455": {"corp_code": "00123456", "stock_kind": "우선주"}
}}}
```

금융위원회 안내상 자료는 일 단위로 갱신됩니다. 출처표시 및 비상업적 이용 조건이 있으며,
상업적 이용은 원천 소유자인 한국예탁결제원과 정보이용계약이 필요합니다.

### 설정

`dividend_config.json`을 수정하거나 `--config 경로`로 부분 설정을 덮어씁니다.

| 설정 | 기본값 | 의미 |
| --- | --- | --- |
| enabled | true | 배당 분석 활성화 |
| dividend_mode | all | all: 정기·특별·종류 미확인 배당 모두 포함, regular_only: 정기배당만 |
| thresholds.drawdown_pct | -30 | 배당 후보의 하락률 상한(%) |
| thresholds.min_yield_pct | 3 | 현재 배당수익률 하한(%) |
| thresholds.min_yield_ratio | 1.2 | 현재 수익률 / 3년 평균 하한 |
| notifications.max_candidates | 10 | 전체 선택 시장에서 발송할 최대 후보 수 |
| notifications.notify_empty | false | 후보 없음 메시지 전송 여부 |
| quality.history_years | 7 | 조회 연수, 최소 6 |
| quality.max_price_age_days | 10 | 최근 종가 경과 일수 상한 |
| quality.max_price_gap_days | 10 | 월말 종가 및 일별 이력 공백 허용 일수 |
| quality.dividend_delay_grace_days | 45 | 통상 배당 간격 이후 지연 허용 일수 |
| quality.quote_tolerance_pct | 10 | 현재가와 최근 이력 종가 차이 허용률 |
| quality.max_yield_pct | 40 | 비정상 수익률 점검 상한(%) |

한국 주식은 시장 공통 설정으로 모두 KRW를 사용합니다. 종목을 추가해도 개별 통화
항목을 만들 필요가 없습니다.

```json
"dividend_reviews": {
  "kr": {"dividend_currency": "KRW"}
}
```

`kr`, `us`, `eu` 키는 시장 전체의 통화 기본값입니다. 시장 공통 항목에는
`dividend_currency`만 지정할 수 있으며, 배당 분류 확인을 의미하지 않습니다.
종목별 공식 자료 대조 결과는 기존 `kr:033780` 같은 키로 별도 기록할 수 있습니다.
시장 기본값에 종목별 설정을 합치며 같은 필드는 종목별 설정을 우선합니다.
`--config`의 `dividend_reviews`도 기본 항목에 병합되므로 Apple 검토 예시를 사용해도
한국 공통 KRW 설정은 유지됩니다.

**현재 기본값 `dividend_mode: all`은 요청에 따라 모든 배당을 합산합니다.**
정기·특별 구분이 미확인인 이벤트도 포함하며, 분류 미확인만으로 계산을 차단하지 않습니다.
이를 정기배당으로 재분류하지는 않으며 원래 구분은 감사 JSON에 보존합니다.
현재 수익률·과거 평균은 총배당 이벤트를, 국내 CAGR·연간 삭감은 DART 사업연도 총배당을 사용합니다.
특별배당으로 지표가 왜곡될 가능성을 CSV·Telegram 경고에 표시하고 계산 기준도 명시합니다.
통화 미확인·불일치, 중복, 비정상 가격 및 이력 부족 등의 검증은 계속 적용합니다.

`regular_only`로 변경하면 이전처럼 정기배당만 계산합니다. 이 모드에서 분류가 미확인인
배당이 있으면 지표는 N/A이고 후보에서 제외됩니다. 공식 자료와 대조한 분류는 아래의
`dividend_reviews`에 기록할 수 있습니다.
국내 DART 연간 자료는 정기·특별배당을 분리하지 못하므로 `regular_only`에서는
연간 배당금·CAGR·연간 삭감을 N/A로 표시합니다. 이벤트 분류가 확인되면 정기배당 TTM·평균은 계산합니다.

`examples/dividend_reviewed.json`은 2026-10-25 Apple 공식 내역과 대조한 실제 검증
설정입니다. `from`~`through`는 **확인한 배당락일 범위**입니다. 공식 페이지의 기준일이나
지급일을 배당락일로 복사하지 마세요. 새로운 배당이 범위를 벗어나거나 조회 범위를
늘려 과거 배당이 추가되면 다시 확인해야 합니다.

```powershell
python daily_runner.py --market all --symbols 033780 AAPL ALV.DE --config examples/dividend_reviewed.json --dry-run --output-dir reports/live_reviewed
```

다음은 **설정 구조 설명용 가상 예시**입니다. `default_kind: regular`는 지정 범위의
모든 배당을 출처와 대조했다는 명시적 확인입니다. 전체를 확인하지 못했다면
`default_kind`를 생략하고 확인한 날짜만 `events`에 넣습니다.

```json
{
  "dividend_reviews": {
    "us:SAMPLE": {
      "from": "2019-01-01",
      "through": "2026-10-25",
      "source": "공식 배당 공시 URL 또는 문서 식별자",
      "dividend_currency": "USD",
      "default_kind": "regular",
      "events": {
        "2024-06-10": {"kind": "special"},
        "2025-06-10": {"kind": "mixed", "regular_amount": 1, "special_amount": 2}
      }
    }
  },
  "symbols": {"kr:123456": "123456.KQ"}
}
```

`events`는 해외에서는 Yahoo 원본의 배당락일을 키로 사용하며 `regular`, `special`, `mixed`,
`unknown`을 지원합니다. 같은 날 합산된 정기·특별배당은 `mixed`로 분리합니다.
국내 검토는 종목별 review에 `"date_basis": "record_date"`를 지정하고 `from`·`through`·
`events`에 배당기준일을 사용합니다. 기존 배당락일 기준 review를 국내 기준일에 적용하지 않습니다.
두 금액의 합계는 원본과 같아야 하며 원본과 같은 주당 기준·단위로 입력합니다.
확실히 구분할 수 없으면 `unknown`으로 남깁니다. 한국 이력 코드는 기본 `코드.KS`이며,
코스닥은 `symbols`에 `kr:코드` → `코드.KQ`를 명시합니다. 다른 종목으로 자동 대체하지
않습니다. 미국·유럽은 기존 입력의 Yahoo 코드를 사용합니다.

### 계산과 데이터 품질

- 분석 기준일은 실행 시점의 한국 날짜입니다. 해외는 거래소 시간대로 배당락일을 복원하며
  국내는 공식 배당기준일의 실제 배당 이벤트를 사용합니다.
- 현재 수익률 = `(기준일 - 12개월, 기준일]` 내 선택 모드의 주당배당금 합계 / 현재가 × 100.
  주기가 달라도 실제 이벤트를 합산하며 분기배당을 단순히 4배 하지 않습니다.
- 3년·5년 평균은 **완료된** 36·60개 달의 각 달력 월말에 TTM을 다시 계산하고,
  그 월말 이전 마지막 거래일 종가로 나눈 월별 수익률의 산술평균입니다. 각 월의
  앞선 12개월까지 이력이 있어야 합니다. 일부 월만 평균 내지 않고 부족하면 N/A입니다.
- 배율은 현재 수익률 / 3년 평균입니다. 표시용 반올림 전 값으로 판정하며
  1.2배·3%·-30% 경계를 포함합니다. 기존 하락 알림은 배당 설정과 독립적으로 -30%를 유지합니다.
- CAGR은 국내는 최근 완료된 6개 DART 사업연도 총배당금, 해외는 6개 달력 연도의 선택 모드 배당 합계로
  `(마지막 연도 / 첫 연도)^(1/5) - 1`을 계산합니다. 예: 2026년 실행은 2020~2025년.
  5개 연도만으로 5년 성장률을 만들지 않습니다. 중간 0원 또는 이력 부족이면 N/A입니다.
  5년 평균·CAGR의 N/A만으로 3년 요건 충족 종목을 제외하지는 않습니다.
- 국내 삭감은 DART 사업연도별 연간 합계의 감소만 점검합니다.
  해외 삭감은 위 연간 합계의 감소 및 현재 TTM의 직전 TTM 대비 감소를 점검합니다.
  경고만 표시하고 자동 제외하지 않습니다. 불충분한 이력은 N/A입니다. 배당락일 변경도
  연간 합계에 영향을 주므로 삭감 경고는 기업 공시로 재확인하세요.
- `all`은 특별배당을 포함하고 `regular_only`는 제외합니다. 분류 미확인은 `regular_only`에서,
  통화 미확인은 두 모드 모두에서 계산값을 N/A로 표시하고 후보에서 제외합니다.
- Yahoo `Close`는 분할 조정 기준이며 배당 차감 조정 `Adj Close`를 사용하지 않습니다.
  Yahoo 배당 이벤트도 같은 분할 기준으로 취급하여 재차 조정하지 않습니다.
  분할·병합 이력을 감사 JSON에 보존합니다. 계산기는 다른 공급자의 원시 주당 자료에 대해
  `basis=raw`일 때 가격과 배당 모두를 이후 분할·병합 비율로 정규화합니다.
- 가격·배당 통화가 다르면 자동 FX 변환 없이 확인 필요로 제외합니다. GBP/GBp/GBX,
  ZAR/ZAc처럼 명시적인 100배 보조 단위만 정규화합니다. CSV에는 원본 현재가 통화와
  정규화된 TTM 배당금 통화를 각각 기록합니다.
- 종가가 없는 날짜(`null` 또는 행 자체가 없음)는 건너뛰며 가격을 임의 보간하지 않습니다.
  월말에는 해당 월말 이전의 마지막 유효 종가를 사용하며, 실제 사용한 날짜를 감사 JSON의
  월별 `price_date`에 기록합니다. 명시적인 `null` 날짜는 `missing_price_dates`와 경고로
  남기되 단독으로 후보를 차단하지 않습니다. 결측이 휴일인지 API 누락인지는 단정하지 않습니다.
  유효 종가 사이 공백 또는 월말 가격 경과일이 `max_price_gap_days`(달력일 기준)를 넘거나
  최근 종가가 `max_price_age_days`를 넘으면 계속 확인 필요로 제외합니다. 장기 휴장에
  맞춰 이 설정을 조정할 수 있으나 거래정지·API 장애도 고려해야 합니다.
- 중복, 음수·0·무한대 가격, 큰 가격 급변, 비정상 수익률도 검출합니다.
  최근 배당 지연은 최근 관측 간격 중앙값과 비교합니다. 과거 이력 공백은 각 시점 이전
  최대 8개 간격의 중앙값(최소 2개 간격 필요)과 비교하여, 현재 반기·분기 주기로 과거 연배당을
  거꾸로 판단하지 않습니다. 정상적인 주기
  변경도 보수적으로 확인 필요가 될 수 있습니다. 무료 API에서 완전히 누락된 이벤트를
  모두 입증할 수는 없으므로 공식 내역 대조가 필요합니다.
- 정상 상태에서 모든 기준을 통과한 종목만 후보입니다. 종목별 조회·계산 실패는 격리하여
  다음 종목과 기존 하락 알림 처리를 계속합니다. 조회 실패가 있으면 종료 코드 1입니다.

### 저장과 자동 실행

기존 `reports/friendly_{market}_52week.csv`, 오류 CSV, 감사 JSON은 유지합니다.

| 신규 파일 | 내용 |
| --- | --- |
| friendly_{market}_dividend.csv | 모든 입력 종목의 지표·판정·경고, UTF-8 BOM |
| friendly_{market}_dividend_errors.csv | 종목별 조회·계산 오류, UTF-8 BOM |
| friendly_{market}_dividend.audit.json | 가격·배당·분할 원본, 출처 URL, 조회 시각, 검토 설정, 월별·연간 계산 |
| daily_runner.log | 배당 상태와 실패 traceback, 누적 기록 |
| telegram_preview.txt | 이번 실행에서 생성한 기존/배당 메시지 본문 |

후보는 전체 시장에서 배율 내림차순으로 정렬하고 설정한 개수만 전송합니다.
`notify_empty`는 배당 알림에만 적용됩니다. 기본값에서는 후보 없음 메시지를 보내지 않습니다.
Actions는 `0 7 * * 3`으로 매주 수요일 16시 KST에 `--market all`을 실행합니다.
GitHub 스케줄 특성상 실제 시작 시각은 지연될 수 있습니다. 수동 실행도 가능합니다.
한국·미국·유럽 결과와 로그를 `stock-results-실행번호` artifact로 저장하고,
업로드 성공 후 이 워크플로의 결과가 있는 최신 2회 실행분만 남깁니다.
다른 워크플로의 artifact와 실행 로그는 정리 대상이 아닙니다.
일수 기준 `retention-days: 2`는 제거했으며, GitHub 저장소의 기본 만료 정책은 적용됩니다.
정리에는 `GITHUB_TOKEN`의 `actions: write` 권한을 사용하고 추가 Secret은 필요 없습니다.
전체 시장 자동 실행이 필요하면 워크플로의 시장 옵션을 별도로 변경할 수 있습니다.
`enabled: false`로 배당 기능만 끌 수 있습니다.

### 검증 기록

2026-10-25 `python -m pytest -q`: **36개 통과**(기존 회귀 테스트 8개 포함).
샘플 테스트는 실제 API·Telegram에 의존하지 않습니다. 월말 TTM, 경계 판정,
연·반기·분기배당, 특별·혼합배당, 삭감·CAGR, 분할·병합, 보조 통화단위,
이력 부족·누락·중복·API 오류와 dry-run 발송 차단 및 기존 알림을 검증했습니다.

실제 KT&G(033780), Apple(AAPL), Allianz(ALV.DE)에서 약 7년 이력을 조회했습니다.
기본 설정에서는 분류·통화 미확인을 올바르게 제외했습니다. KT&G에는 결측 종가도
관측되어 추가 확인 필요로 기록되었습니다. 검토 설정의 Apple은 TTM 1.06 USD,
현재 수익률 약 0.30%, 3년 평균 약 0.44%, 5년 평균 약 0.49%, 배율 약 0.6996,
5년 CAGR 약 4.99%로 계산되었고 선정 기준 미충족이었습니다.

Apple 검토 근거: [공식 배당 이력](https://investor.apple.com/dividend-history/).
공식 원시 배당금의 2020년 분할 이전 금액을 4로 나누어 Yahoo 이벤트와 대조했습니다.
다른 두 종목의 정기·특별 구분은 확정하지 않았으며 정상 후보로 취급하지 않았습니다.
실데이터 결과는 `reports/live_reviewed/`, 후보 없음 메시지는 그 안의
`telegram_preview.txt`, 요청된 후보 출력 형식의 **가상 샘플**은
`reports/sample_telegram_preview.txt`에서 확인할 수 있습니다. 실제 전송과 push는
수행하지 않았습니다. reports는 기존 정책에 따라 Git에서 제외됩니다.
