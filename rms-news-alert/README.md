# 에르메스(RMS) 공식 실적 발표 알림

에르메스 IR의 영문 공시 목록과 PDF를 읽어 매출, 전년 동기 대비 성장률(보고·고정환율), 반기·연간 이익을 한글로 텔레그램에 보냅니다. OpenAI API 키나 별도 파이썬 패키지는 필요 없습니다.

## 실행
Python 3.10 이상과 Poppler의 `pdftotext`를 PATH에 설치합니다. 저장소 루트에서 실행합니다.

```sh
python3 rms-news-alert/alert.py preview
python3 -m unittest discover -s rms-news-alert
python3 rms-news-alert/alert.py check
```

`preview`는 공식 최신 자료를 출력합니다. `check`는 환경변수 `TELEGRAM_BOT_TOKEN` 및 `TELEGRAM_CHAT_ID`를 사용합니다. 첫 실행은 최신 발표 한 건을 보내고, 이전 발표는 기준 목록에만 기록합니다. 이후 미발송 실적을 날짜순으로 보냅니다. 공식 표에서 읽을 수 없는 값은 생략하고 원문 링크를 제공합니다.

## GitHub Actions
`.github/workflows/rms-ir.yml`이 한국시간 매일 16:17, 22:17에 확인합니다. Actions의 **RMS official IR Telegram → Run workflow**에서도 수동 확인할 수 있습니다. 최초 설치 시 코드 푸시가 첫 발송을 시작합니다. 같은 저장소의 Telegram Secrets를 사용하며, 동시 실행은 직렬화합니다.

Bot API가 성공 응답으로 돌려준 메시지 번호를 `rms-news-alert/delivery.json`에 저장하고 커밋합니다. 실제 전송 직후 통신이 끊기거나 이력 커밋이 실패하면 채팅의 마지막 공시와 실행 로그를 확인한 뒤 재시도하세요. 같은 기간의 PDF 재업로드는 새 발표로 발송하지 않습니다.
