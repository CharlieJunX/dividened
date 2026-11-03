# AI 투자 리포터

기존 `investment.audit.json`의 수치를 재사용해 한국어 투자 검토 보고서를 생성합니다.
점수와 검토 상태는 코드가 결정하고, AI는 검증된 발췌 범위에서 설명합니다.

## 실행

프로젝트 루트의 VS Code PowerShell 터미널에서 실행합니다.

```powershell
.\.venv\Scripts\python.exe ai_report_runner.py --demo --offline
.\.venv\Scripts\python.exe ai_report_runner.py --candidates --top 5 --offline
.\.venv\Scripts\python.exe ai_report_runner.py --symbol us:IBM --dry-run
.\.venv\Scripts\python.exe ai_report_runner.py --symbol us:IBM --fetch-docs --use-ai --dry-run
.\.venv\Scripts\python.exe daily_runner.py --dry-run --ai-reports
```

`--demo`는 가상 기업 3개를 사용하며 AI를 호출하지 않습니다.
`--offline`은 기존 자료만 사용합니다. `--dry-run`은 Telegram 전송만 막으므로
`--use-ai`와 함께 실행하면 유료 API 호출이 가능합니다.

`reports/ai_reports/<보고서ID>/`에 HTML, Markdown, JSON, 입력 스냅샷,
검증 결과, 설정 및 Telegram 미리보기가 저장됩니다. 이전 보고서는 보존합니다.
생성된 HTML은 브라우저로 열어 확인합니다.

## 설정과 자격증명

기존 `.env`에 `OPENAI_API_KEY`를 설정합니다. 키는 소스와 로그에 기록하지 않습니다.
공식 공시 수집에는 실제 연락처가 포함된 `SEC_USER_AGENT`, 한국 목록 조회에는
`OPENDART_API_KEY`가 필요합니다. `--env-file`로 별도 파일을 지정할 수 있습니다.

`ai_report_config.json`의 `provider.model`은 계정에서 접근 가능한 모델로 지정합니다.
모델 접근 가능 여부와 실제 청구 비용은 라이브 호출로 검증하지 않았습니다.
`provider.pricing`은 예산 계산 입력값이며 사용 전 공식 가격표와 대조해야 합니다.
설정된 가격에 따른 추정치이지 실제 청구액이나 비용 상한 보장이 아닙니다.
일별 호출·종목별 호출·추정 비용 한도를 적용하며 실패 요청도 호출 횟수에 포함합니다.
재시도에서 사용한 토큰을 합산합니다. 일별 한도 기준은 UTC입니다.

기본 AI 사용은 비활성화되어 있으며 `--use-ai`로 활성화합니다.
키 누락·한도 초과·생성 실패·인용 검증 실패 시 정량 보고서로 대체합니다.

## 기업 식별과 공시

동일 티커가 여러 시장에 있으면 `us:IBM`처럼 시장을 명시합니다.
`identifiers`에 `us:IBM` 등의 키로 `exchange`, `security_type`,
`reporting_currency`를 등록할 수 있습니다. 검증하지 못한 거래소·증권 유형은
UNKNOWN 또는 UNVERIFIED로 표시하며 임의로 확정하지 않습니다.

`official_disclosures`에는 시장별 내부 ID를 키로 SEC `cik` 또는 DART
`corp_code`를 설정할 수 있습니다. 공식 조회는 `--fetch-docs`에서만 수행합니다.
SEC는 목록과 제한된 원문 발췌를 조회합니다. DART는 현재 공시 목록만 조회하며
원문 내용 확인으로 취급하지 않습니다. 유럽 및 일반 뉴스 자동 수집은 미구현입니다.

한국·유럽 등 별도 검증 문서는 `documents_file`이 가리키는 JSON 배열로 입력합니다.
필드는 `ai_reporter/evidence.py`의 `validate_document`를 따릅니다.
제목·URL만으로는 본문 인용이 허용되지 않습니다. 실제 원문에서 확인한 발췌,
발췌 SHA256, 원문 SHA256, 공개 시각, 수집 시각, 기업 식별자를 함께 기록합니다.
해시는 내용 동일성 확인용이며 출처의 진실성을 보증하지 않습니다.

인용 검증은 정확한 발췌 일치 위주의 보수적인 방식입니다. 자유로운 번역·요약이나
인과관계에 대한 의미적 검증을 보증하지 않습니다. 요약과 시나리오 등은 검증된
정량 템플릿으로 대체하며, 자료가 없으면 하락 원인을 확인 불가로 표시합니다.

## 백테스트와 한계

동일 기업 식별자·국가·기본 배당 전략에 대응하는 비가상 결과만 참고합니다.
평가 종료일 또는 실행일이 분석 기준일 이후인 결과는 제외합니다.
표본 성과를 해당 종목의 미래 상승 확률로 해석하지 않습니다.
유효 결과가 없으면 평가 불가로 표시합니다. 현재 구현은 과거 시점 재구성 전용
도구가 아니며 오래된 정량 자료는 허용 일수에 따라 거절합니다.

## Telegram 및 자동화

기본 실행과 `--ai-reports` 연동은 보고서만 생성합니다.
확인한 보고서의 명시적 전송 명령은 다음과 같습니다.

```powershell
.\.venv\Scripts\python.exe ai_report_runner.py --report-id <보고서ID> --send-telegram
```

Telegram의 실제 전송은 별도로 실행해야 하며 전송 성공 기록을 확인해 중복을 막습니다.
전송 결과가 불확실하면 자동 재전송하지 않습니다. 채팅에서 확인한 뒤에만
`--retry-uncertain`을 사용할 수 있습니다. 메시지는 기존 sender의 일반 텍스트로 전송합니다.
기존 Actions 스케줄은 변경하지 않았습니다.

## 검증

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

테스트는 모의 API 응답을 사용합니다. 실제 API 품질·실제 기사 근거·실제 전송은 별도 검증 대상입니다.
구조화 응답 API 참고: https://developers.openai.com/api/docs/guides/structured-outputs
