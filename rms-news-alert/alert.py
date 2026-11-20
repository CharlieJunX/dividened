"""Hermès official earnings alerts. Python 3.10+ and poppler pdftotext."""
import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

SITE = "https://finance.hermes.com"
INDEX = SITE + "/page-data/en/press-releases-and-news/page-data.json"
STATE = Path(__file__).with_name("delivery.json")
PERIODS = {"first quarter": "1분기", "third quarter": "3분기",
           "half year": "상반기", "full year": "연간"}


def get(url):
    with urlopen(Request(url, headers={"User-Agent": "RMS-IR-alert/1.0"}), timeout=35) as response:
        return response.read()


@dataclass(frozen=True)
class Release:
    key: str
    period: str
    year: str
    published: str
    url: str


def discover():
    hashes = json.loads(get(INDEX))["staticQueryHashes"]
    # Gatsby's public document catalogue. Rediscover it if the site changes its hash.
    usual = "325149558"
    for key in sorted(hashes, key=lambda value: value != usual):
        data = json.loads(get(f"{SITE}/page-data/sq/d/{int(key)}.json"))["data"]
        if "allDocument" in data:
            nodes = data["allDocument"]["nodes"]
            break
    else:
        raise ValueError("공식 IR 문서 목록 구조가 변경되었습니다.")
    releases = {}
    now = datetime.now(timezone.utc)
    for node in nodes:
        en = node.get("en") or {}
        if not en.get("status") or "19" not in (en.get("category_id") or []):
            continue
        title = re.sub(r"[-–—]", " ", en.get("title") or "").lower()
        period = next((k for k in PERIODS if k in title), None)
        year = re.search(r"\b20\d{2}\b", title)
        if not period or not year or not re.search(r"\b(revenue|results|sales)\b", title):
            continue
        date = en.get("datetime_publication")
        url = node.get("fileUrlEn") or ""
        if not date or not url:
            continue
        published = datetime.fromisoformat(date.replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        if published > now:
            continue
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "assets-finance.hermes.com" or not parsed.path.lower().endswith(".pdf"):
            raise ValueError("공식 PDF 주소 형식을 확인하세요.")
        key = f"{year[0]}-{period}"
        item = Release(key, period, year[0], date[:10], url)
        if key not in releases or releases[key].published < item.published:
            releases[key] = item
    if not releases:
        raise ValueError("공식 IR 실적 자료를 찾지 못했습니다.")
    return sorted(releases.values(), key=lambda r: (r.published, r.key))


def amount(value):
    return f"{Decimal(value.replace(',', '')) / 100:,.2f}억 유로"


def rate(value):
    num = Decimal(value.strip("()%")) * (-1 if "(" in value else 1)
    return f"{num:+.1f}%" if num else "0.0%"


def summary(item, pdf_text):
    # First geography table is the reporting period (nine months for Q3).
    # Select the quarter by its header, never infer it from the order of totals.
    pages = pdf_text.split("\f")
    geo = next((p for p in pages if "REVENUE BY GEOGRAPHICAL AREA" in p.upper()
                and re.search(r"(?m)^\s*TOTAL\s+\d", p)), "")
    pattern = r"(?m)^[ \t]*TOTAL[ \t]+([\d,]+)[ \t]+[\d,]+[ \t]+([()\d.%+\-]+)[ \t]+([()\d.%+\-]+)"
    def sales(table, label):
        row = re.search(pattern, table)
        if not row:
            return None
        value, current, constant = row.groups()
        return f"{label} 매출 {amount(value)} (전년 대비 {rate(current)}, 고정환율 {rate(constant)})"
    period = PERIODS[item.period]
    if item.period == "third quarter":
        section = re.search(r"\b(?:3rd|third) quarter\b[ \t]+Evolution", geo, re.I)
        rows = [sales(geo[section.end():], period) if section else None, sales(geo, "1~9월 누적")]
    else:
        rows = [sales(geo, period)]
    if item.period in ("half year", "full year"):
        # Only use key figures whose heading starts with the requested period.
        heading = ("H1 " if item.period == "half year" else "") + item.year
        match = re.search(r"(?im)^[ \t]*In millions of euros[ \t]+" + heading + r"\b", pdf_text)
        block = pdf_text[match.end():].split("\f")[0] if match else ""
        for label, term in [("경상영업이익", r"Recurring operating income"), ("지배주주 순이익", r"Net profit [–-] Group share")]:
            number = re.search(r"(?im)^[ \t]*" + term + r"(?: \(\d+\))?[ \t]+([\d,]+)\b", block)
            if number:
                rows.append(f"{label} {amount(number[1])}")
    return [r for r in rows if r]


def message(item):
    pdf = get(item.url)
    if not pdf.startswith(b"%PDF-"):
        raise ValueError("IR 응답이 PDF가 아닙니다.")
    proc = subprocess.run(["pdftotext", "-layout", "-", "-"], input=pdf,
                          stdout=subprocess.PIPE, check=True, timeout=30)
    rows = summary(item, proc.stdout.decode("utf-8"))
    if not rows:
        rows = ["공식 PDF에서 실적 수치를 확인해 주세요. 표 형식이 달라 자동 추출을 생략했습니다."]
    return "\n".join([f"[RMS 공식 IR] 에르메스 {item.year}년 {PERIODS[item.period]} 실적",
                      f"발표일 {item.published}", *rows, f"원문 {item.url}"])


def send(body):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise ValueError("TELEGRAM_BOT_TOKEN 및 TELEGRAM_CHAT_ID 설정이 필요합니다.")
    payload = {"chat_id": chat, "text": body, "link_preview_options": {"is_disabled": True}}
    request = Request(f"https://api.telegram.org/bot{token}/sendMessage",
                      data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            receipt = json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"Telegram HTTP {error.code}") from None
    except (OSError, ValueError):
        raise RuntimeError("Telegram 응답 확인 실패. 재시도 전 채팅 기록을 확인하세요.") from None
    if not receipt.get("ok") or not receipt.get("result", {}).get("message_id"):
        raise RuntimeError("Telegram 발송 확인 번호를 받지 못했습니다.")
    return receipt["result"]["message_id"]


def run(mode, path):
    releases = discover()
    if mode == "preview":
        print(message(releases[-1]))
        return
    # First run only sends the latest release; afterwards catch up in date order.
    delivered = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(delivered, dict):
        raise ValueError("발송 이력 형식을 확인하세요.")
    if not delivered:
        pending = releases[-1:]
    else:
        pending = [r for r in releases if r.key not in delivered]
    for release in pending:
        receipt = send(message(release))
        if not delivered:
            delivered.update({r.key: "baseline" for r in releases[:-1]})
        delivered[release.key] = receipt
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(delivered, sort_keys=True, indent=2) + "\n")
        tmp.replace(path)
        print(f"Telegram accepted {release.key}; message_id={receipt}", flush=True)
    if not pending:
        print("새 실적 발표 없음")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["preview", "check"])
    parser.add_argument("--state", type=Path, default=STATE)
    args = parser.parse_args()
    try:
        run(args.mode, args.state)
    except HTTPError as error:
        sys.exit(f"공식 IR HTTP {error.code}")
    except (URLError, ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
