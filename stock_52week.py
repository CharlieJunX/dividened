"""한국·미국·유럽 종목의 52주 최고가 대비 현재가 등락률 저장."""

import argparse
import csv
import json
import io
import re
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


HEADERS = ["종목코드", "회사명", "등급", "약점(리스크)", "최근52주 최고가대비 현재가 등락율(%)"]
BASE = "https://m.stock.naver.com/api/stock"


def read_stocks(path, market="kr"):
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp949")
    codes = []
    for line, row in enumerate(csv.reader(io.StringIO(text, newline="")), 1):
        if not row or not any(cell.strip() for cell in row):
            continue
        code = row[0].strip()
        if code.casefold() in {"종목코드", "종목코드 6자리", "code", "symbol", "ticker"}:
            continue
        pattern = r"[0-9]{6}" if market == "kr" else r"[A-Za-z0-9][A-Za-z0-9.\-^=]*"
        if not re.fullmatch(pattern, code):
            raise ValueError(f"{line}행: 유효하지 않은 {market} 종목코드: {code!r}")
        if len(row) > 4:
            raise ValueError(f"{line}행: 입력 열은 종목코드, 회사명, 등급, 약점(리스크)이어야 합니다. 쉼표가 포함된 값은 큰따옴표로 감싸세요.")
        name = row[1].strip() if len(row) >= 3 else ""
        grade = row[2].strip() if len(row) >= 3 else row[1].strip() if len(row) == 2 else ""
        risk = row[3] if len(row) == 4 else ""
        codes.append((code, name, grade, risk))
    if not codes:
        raise ValueError("입력 파일에 종목코드가 없습니다.")
    return codes


def fetch(code, endpoint):
    request = Request(f"{BASE}/{code}/{endpoint}", headers={
        "User-Agent": "Mozilla/5.0", "Accept": "application/json",
        "Referer": f"https://m.stock.naver.com/domestic/stock/{code}",
    })
    for attempt in range(3):
        try:
            with urlopen(request, timeout=20) as response:
                data = json.load(response)
            if data.get("itemCode") != code:
                raise ValueError("응답 종목코드 불일치 또는 조회 불가")
            return data
        except (OSError, ValueError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def price(value):
    number = Decimal(str(value).replace(",", ""))
    if not number.is_finite() or number <= 0:
        raise ValueError(f"유효하지 않은 가격: {value!r}")
    return number


def overseas_quote(code):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(code, safe='')}?interval=1d&range=5d"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=20) as response:
                data = json.load(response)
            chart = data["chart"]
            if chart.get("error") or not chart.get("result"):
                raise ValueError(f"시세 조회 불가: {chart.get('error')}")
            meta = chart["result"][0]["meta"]
            if meta.get("symbol", "").casefold() != code.casefold():
                raise ValueError("응답 종목코드 불일치")
            return {
                "name": meta.get("longName") or meta.get("shortName") or code,
                "current": meta["regularMarketPrice"],
                "high_52week": meta["fiftyTwoWeekHigh"],
                "traded_at": datetime.fromtimestamp(meta["regularMarketTime"], timezone.utc).isoformat(),
                "currency": meta.get("currency"), "exchange": meta.get("fullExchangeName"),
                "source": "Yahoo Finance", "source_url": url,
            }
        except (OSError, ValueError, KeyError, TypeError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def get_quote(code, market):
    if market != "kr":
        return overseas_quote(code)
    basic = fetch(code, "basic")
    info = fetch(code, "integration")
    fields = {item["code"]: item["value"] for item in info["totalInfos"]}
    return {"name": basic["stockName"], "current": basic["closePrice"],
            "high_52week": fields["highPriceOf52Weeks"],
            "traded_at": basic.get("localTradedAt"), "currency": "KRW",
            "source": "Naver Finance"}


def rate(current, high):
    return ((price(current) / price(high) - 1) * 100).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def sort_result_rows(rows):
    """등락률 오름차순. 공란은 마지막에 두고 동률은 입력 순서를 유지."""
    return sorted(rows, key=lambda row: (
        row[-1] == "", Decimal(row[-1]) if row[-1] != "" else Decimal(0)
    ))


def write_csv(path, headers, rows):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)
    temporary.replace(path)


def run_market(market, codes, output):
    rows, errors, audit = [], [], []
    for index, (code, input_name, grade, risk) in enumerate(codes, 1):
        name = input_name
        try:
            snapshot = get_quote(code, market)
            name = input_name or snapshot["name"]
            current, high = snapshot["current"], snapshot["high_52week"]
            change = str(rate(current, high))
            rows.append([code, name, grade, risk, change])
            audit.append({**snapshot, "code": code, "name": name, "grade": grade, "risk": risk, "market": market,
                          "retrieved_at": datetime.now(timezone.utc).isoformat()})
            print(f"[{index}/{len(codes)}] {code} {name}: {change}%", flush=True)
        except (OSError, ValueError, KeyError, TypeError, ArithmeticError) as exc:
            rows.append([code, name, grade, risk, ""])
            errors.append([code, grade, str(exc)])
            print(f"[{index}/{len(codes)}] {code}: 조회 실패 - {exc}", file=sys.stderr, flush=True)
        time.sleep(0.2)
    write_csv(output, HEADERS, sort_result_rows(rows))
    write_csv(output.with_name(output.stem + "_errors.csv"), ["종목코드", "등급", "오류"], errors)
    output.with_suffix(".audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"저장: {output} (성공 {len(codes) - len(errors)}, 실패 {len(errors)})")
    return 1 if errors else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("market", nargs="?", choices=["kr", "us", "eu", "all"], help="시장 (기본: kr)")
    parser.add_argument("--market", dest="market_option", choices=["kr", "us", "eu", "all"])
    parser.add_argument("--input", type=Path, help="단일 시장의 입력 파일 지정")
    parser.add_argument("--output", type=Path, help="단일 시장의 출력 파일 지정")
    args = parser.parse_args()
    if args.market and args.market_option and args.market != args.market_option:
        parser.error("시장 옵션이 서로 다릅니다.")
    selected = args.market_option or args.market or "kr"
    if selected == "all" and (args.input or args.output):
        parser.error("all에서는 --input/--output을 사용할 수 없습니다.")
    markets = ["kr", "us", "eu"] if selected == "all" else [selected]
    root = Path(__file__).resolve().parent
    jobs = []
    for market in markets:
        source = args.input or root / f"friendly_{market}.csv"
        output = args.output or root / f"friendly_{market}_52week.csv"
        if source.resolve() == output.resolve():
            parser.error("입력 파일과 출력 파일은 달라야 합니다.")
        try:
            jobs.append((market, read_stocks(source, market), output))
        except (OSError, ValueError) as exc:
            parser.exit(2, f"{market} 입력 오류: {exc}\n")
    status = 0
    for market, stocks, output in jobs:
        try:
            status = max(status, run_market(market, stocks, output))
        except OSError as exc:
            print(f"{market} 파일 저장 실패: {exc}. Excel 등에서 파일을 열었다면 닫고 다시 실행하세요.", file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
