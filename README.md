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
