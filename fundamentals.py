"""종목 기본 지표 조사.

SEC XBRL 재무 데이터와 현재 주가로 아래 6개 지표를 계산합니다.
  PER, PBR, 매출 성장률, 영업이익률, 잉여현금흐름(FCF), 부채비율
각 수치에는 원본 공시 링크가 붙어 있어 직접 대조할 수 있습니다.

사용법:
  python fundamentals.py AAPL MSFT        # 종목 지정
  python fundamentals.py --overlap        # 13F 리포트의 중복 매수 종목
  python fundamentals.py --new-buys       # 13F 리포트의 모든 신규 매수 종목
"""
import argparse
import csv
import sys
from datetime import date, datetime, timedelta

import requests

from tracker import DATA, REPORTS, Sec, load_config, load_json, save_json

OUT = REPORTS / "fundamentals"
TICKER_MAP = DATA / "company_tickers.json"

FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "20-F/A"}
TAGS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet"],
    "op_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "liab_and_equity": ["LiabilitiesAndStockholdersEquity"],
}
# 설비투자를 세부 항목으로 나눠 공시하는 회사용 (델타: 항공기, 에퀴닉스: 부동산 등)
CAPEX_PARTS = ["PaymentsToAcquireOtherPropertyPlantAndEquipment", "PaymentsForFlightEquipment",
               "PaymentsToAcquireOtherProductiveAssets", "PaymentsToAcquireRealEstate"]
LABELS = {"revenue": "매출", "op_income": "영업이익", "net_income": "순이익", "cfo": "영업현금흐름",
          "capex": "설비투자(CapEx)", "liabilities": "총부채", "equity": "자기자본"}


# ---------------------------------------------------------------- 데이터 받기

def ticker_to_cik(sec, ticker):
    m = load_json(TICKER_MAP, None)
    if m is None or not m.get("_date") or m["_date"] < str(date.today() - timedelta(days=7)):
        raw = sec.get("https://www.sec.gov/files/company_tickers.json").json()
        m = {v["ticker"].upper(): {"cik": v["cik_str"], "name": v["title"]} for v in raw.values()}
        m["_date"] = str(date.today())
        save_json(TICKER_MAP, m)
    return m.get(ticker.upper().replace(".", "-"))


def get_price(ticker):
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker.replace('.', '-')}",
                     params={"range": "5d", "interval": "1d"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    r.raise_for_status()
    meta = r.json()["chart"]["result"][0]["meta"]
    return meta["regularMarketPrice"], datetime.fromtimestamp(meta["regularMarketTime"]).date()


def d(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def tag_rows(gaap, tag, unit="USD"):
    """한 태그의 값을 (시작일, 종료일)별 최신 제출값으로 정리합니다."""
    dedup = {}
    for r in gaap.get(tag, {}).get("units", {}).get(unit, []):
        if r.get("form") not in FORMS:
            continue
        k = (r.get("start"), r["end"])
        if k not in dedup or r["filed"] > dedup[k]["filed"]:
            dedup[k] = {**r, "tag": tag}
    return dedup


def load_facts(gaap, key, unit="USD"):
    """후보 태그들을 합쳐 기간별 값 목록을 만듭니다.
    회사가 중간에 태그를 바꿔도 이어지도록, 같은 기간에 값이 여러 개면 우선순위가 높은 태그를 씁니다.
    설비투자는 주 태그가 없는 기간에 한해 세부 항목(항공기, 부동산 등)을 합산합니다."""
    merged = {}
    for tag in TAGS[key]:
        for k, r in tag_rows(gaap, tag, unit).items():
            merged.setdefault(k, r)
    if key == "capex":
        parts = [tag_rows(gaap, t, unit) for t in CAPEX_PARTS]
        for k in set().union(*parts):
            if k in merged or not k[0]:
                continue
            found = [p[k] for p in parts if k in p]
            merged[k] = {**max(found, key=lambda r: r["filed"]), "val": sum(r["val"] for r in found),
                         "tag": " + ".join(r["tag"] for r in found)}
    if not merged:
        return None, []
    facts = []
    for r in merged.values():
        f = {**r, "end_d": d(r["end"])}
        if r.get("start"):
            f["start_d"] = d(r["start"])
            f["days"] = (f["end_d"] - f["start_d"]).days
        facts.append(f)
    facts.sort(key=lambda x: x["end"])
    return facts[-1]["tag"], facts


# ---------------------------------------------------------------- 기간 계산

def near(a, b, tol):
    return abs((a - b).days) <= tol


def ttm(facts, end):
    """end 시점까지 최근 12개월 합계와 계산에 쓴 원본 사실들을 돌려줍니다.
    10-Q의 현금흐름표는 누적(YTD)값만 있어서 '직전 연간 + 올해 누적 - 작년 같은 기간 누적'으로 계산합니다."""
    at_end = [f for f in facts if "days" in f and f["end_d"] == end]
    annual = [f for f in at_end if 350 <= f["days"] <= 380]
    if annual:
        return annual[0]["val"], [annual[0]]
    ytd = sorted((f for f in at_end if 80 <= f["days"] < 350), key=lambda f: -f["days"])
    if ytd:
        y = ytd[0]
        fy = next((f for f in facts if "days" in f and 350 <= f["days"] <= 380
                   and near(f["end_d"], y["start_d"] - timedelta(days=1), 7)), None)
        prev = next((f for f in facts if "days" in f and near(f["end_d"], y["end_d"] - timedelta(days=365), 10)
                     and abs(f["days"] - y["days"]) <= 10), None)
        if fy and prev:
            return fy["val"] + y["val"] - prev["val"], [y, fy, prev]
    q = quarters(facts)
    q = [x for x in q if x["end_d"] <= end][-4:]
    if len(q) == 4 and near(q[-1]["end_d"], end, 3) and near(q[0]["end_d"], end - timedelta(days=273), 15):
        return sum(x["val"] for x in q), q
    return None, []


def quarters(facts):
    """분기(약 3개월) 값 목록. 4분기는 10-K에 따로 없어서 '연간 - 1~3분기'로 만듭니다."""
    qs = {f["end_d"]: f for f in facts if "days" in f and 80 <= f["days"] <= 100}
    for fy in (f for f in facts if "days" in f and 350 <= f["days"] <= 380):
        if fy["end_d"] in qs:
            continue
        inside = [q for q in qs.values() if q["start_d"] >= fy["start_d"] - timedelta(days=3) and q["end_d"] < fy["end_d"]]
        if len(inside) == 3:
            qs[fy["end_d"]] = {**fy, "val": fy["val"] - sum(q["val"] for q in inside), "derived": True,
                               "start_d": max(q["end_d"] for q in inside) + timedelta(days=1)}
    return sorted(qs.values(), key=lambda q: q["end_d"])


def latest_instant(facts):
    inst = [f for f in facts if "days" not in f]
    return inst[-1] if inst else None


def latest_duration_end(facts):
    ends = [f["end_d"] for f in facts if "days" in f and 80 <= f["days"] <= 380]
    return max(ends) if ends else None


def shares_outstanding(company, period_end):
    """발행주식 수. 표지(dei) 값이 오래됐거나 없으면(주식 종류가 여러 개인 회사 등) 재무제표 값으로 대신합니다."""
    facts = company["facts"]
    cutoff = str(period_end - timedelta(days=200))

    rows = [r for r in facts.get("dei", {}).get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", [])]
    if rows:
        last = max(r["end"] for r in rows)
        if last >= cutoff:
            # 주식 종류가 여러 개면(A/B주 등) 같은 공시의 같은 날짜 값을 합산
            accn = max((r for r in rows if r["end"] == last), key=lambda r: r["filed"])["accn"]
            vals = [r for r in rows if r["end"] == last and r["accn"] == accn]
            return sum(r["val"] for r in vals), vals

    gaap = facts.get("us-gaap", {})
    for tag in ("CommonStockSharesOutstanding", "WeightedAverageNumberOfDilutedSharesOutstanding"):
        alt = [r for r in gaap.get(tag, {}).get("units", {}).get("shares", [])
               if r.get("form") in FORMS and r["end"] >= cutoff]
        if alt:
            last = max(alt, key=lambda r: (r["end"], r["filed"]))
            return last["val"], [{**last, "tag": tag}]
    return None, None


# ---------------------------------------------------------------- 지표 계산

def analyze(sec, ticker):
    info = ticker_to_cik(sec, ticker)
    if not info:
        raise ValueError(f"{ticker}: SEC에 등록된 티커가 아닙니다 (외국 기업이거나 ETF일 수 있음)")
    cik = info["cik"]
    company = sec.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json").json()
    gaap = company["facts"].get("us-gaap")
    if not gaap:
        raise ValueError(f"{ticker}: 미국 회계기준(US-GAAP) 재무 데이터가 없습니다 (IFRS를 쓰는 외국 기업 등)")

    tags, facts = {}, {}
    for key in TAGS:
        tags[key], facts[key] = load_facts(gaap, key)

    end = latest_duration_end(facts["revenue"]) or latest_duration_end(facts["net_income"])
    if not end:
        raise ValueError(f"{ticker}: 손익 데이터를 찾지 못했습니다")
    prev_ends = [f["end_d"] for f in facts["revenue"] if near(f["end_d"], end - timedelta(days=365), 10)]
    prev_end = prev_ends[0] if prev_ends else None

    sources = []  # (항목, 값, 원본 사실 목록)

    def t(key, at):
        val, src = ttm(facts[key], at) if facts[key] and at else (None, [])
        if val is not None and at == end:
            sources.append((LABELS[key] + " (최근 12개월)", val, src))
        return val

    rev, opi, ni, cfo, capex = (t(k, end) for k in ("revenue", "op_income", "net_income", "cfo", "capex"))
    rev_prev = ttm(facts["revenue"], prev_end)[0] if prev_end else None
    if rev_prev is not None:
        sources.append(("매출 (전년 같은 기간 12개월)", rev_prev, ttm(facts["revenue"], prev_end)[1]))

    eq = latest_instant(facts["equity"])
    liab = latest_instant(facts["liabilities"])
    liab_val = liab["val"] if liab else None
    if eq:
        sources.append(("자기자본", eq["val"], [eq]))
    if liab:
        sources.append(("총부채", liab["val"], [liab]))
    elif eq:
        le = next((f for f in facts["liab_and_equity"] if "days" not in f and f["end"] == eq["end"]), None)
        if le:
            liab_val = le["val"] - eq["val"]
            sources.append(("총부채 (부채와자본총계 - 자기자본)", liab_val, [le, eq]))

    shares, share_src = shares_outstanding(company, end)
    try:
        price, price_date = get_price(ticker)
    except (requests.RequestException, KeyError, TypeError):
        price, price_date = None, None
    mcap = price * shares if price and shares else None
    if shares:
        sources.append(("발행주식 수", shares, share_src))

    def ratio(a, b):
        return a / b if a is not None and b not in (None, 0) else None

    fcf = cfo - capex if cfo is not None and capex is not None else None
    m = {
        "ticker": ticker.upper(), "name": info["name"], "cik": cik,
        "period_end": str(end), "price": price, "price_date": str(price_date) if price_date else None,
        "market_cap": mcap,
        "per": ratio(mcap, ni) if ni and ni > 0 else None, "per_note": "적자" if ni is not None and ni <= 0 else "",
        "pbr": ratio(mcap, eq["val"]) if eq and eq["val"] > 0 else None,
        "revenue_growth": ratio(rev, rev_prev) - 1 if ratio(rev, rev_prev) else None,
        "op_margin": ratio(opi, rev),
        "fcf": fcf, "fcf_margin": ratio(fcf, rev),
        "debt_ratio": ratio(liab_val, eq["val"]) * 100 if eq and eq["val"] > 0 and liab_val is not None else None,
        "revenue": rev, "op_income": opi, "net_income": ni,
        "multi_class": shares_src_is_multi(share_src),
        **{k + "_tag": tags[k] for k in ("op_income", "cfo", "capex", "net_income")},
    }

    rq = {q["end_d"]: q for q in quarters(facts["revenue"])}
    oq = {q["end_d"]: q for q in quarters(facts["op_income"])}
    m["quarters"] = [{"end": str(e), "revenue": rq[e]["val"], "op_income": oq[e]["val"] if e in oq else None}
                     for e in sorted(rq)[-8:]]
    m["sources"] = sources
    return m


def shares_src_is_multi(src):
    return bool(src) and len(src) > 1


# ---------------------------------------------------------------- 출력

def money(v):
    if v is None:
        return "-"
    s = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1e9:
        return f"{s}${v / 1e9:,.2f}B"
    if v >= 1e6:
        return f"{s}${v / 1e6:,.1f}M"
    return f"{s}${v:,.0f}"


def pct(v):
    return "-" if v is None else f"{v * 100:.1f}%"


def num(v, suffix=""):
    return "-" if v is None else f"{v:,.1f}{suffix}"


def link(cik, accn):
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace('-', '')}/"


def to_markdown(m):
    L = [f"# {m['ticker']} — {m['name']}", "",
         f"재무 기준일: {m['period_end']} (최근 12개월)  |  주가: {money(m['price']) if m['price'] else '-'} "
         f"({m['price_date'] or '조회 실패'})  |  시가총액: {money(m['market_cap'])}", "",
         "## 지표", "", "| 지표 | 값 | 계산 |", "|---|---|---|",
         f"| PER | {num(m['per'], '배') if m['per'] else (m['per_note'] or '-')} | 시가총액 ÷ 최근 12개월 순이익 |",
         f"| PBR | {num(m['pbr'], '배')} | 시가총액 ÷ 자기자본 |",
         f"| 매출 성장률 | {pct(m['revenue_growth'])} | 최근 12개월 매출 ÷ 1년 전 12개월 매출 - 1 |",
         f"| 영업이익률 | {pct(m['op_margin'])} | 영업이익 ÷ 매출 (최근 12개월) |",
         f"| 잉여현금흐름 | {money(m['fcf'])} (매출 대비 {pct(m['fcf_margin'])}) | 영업현금흐름 - 설비투자 |",
         f"| 부채비율 | {num(m['debt_ratio'], '%')} | 총부채 ÷ 자기자본 × 100 |", ""]
    missing = [name for name, key in (("영업이익", "op_income"), ("영업현금흐름", "cfo"), ("설비투자", "capex"),
                                      ("순이익", "net_income")) if m[key + "_tag"] is None]
    if missing:
        L += [f"> 공시에 {', '.join(missing)} 항목이 없어 일부 지표를 계산하지 못했습니다. "
              "은행·보험·건설사처럼 손익계산서 구조가 다른 업종에서 흔합니다.", ""]
    if m["multi_class"]:
        L += ["> 주의: 주식 종류가 여러 개(A/B주 등)입니다. 종류별 주가가 다르면 시가총액과 PER/PBR이 틀릴 수 있습니다.", ""]
    if m["quarters"]:
        L += ["## 분기별 매출과 영업이익률", "", "| 분기 말 | 매출 | 영업이익 | 영업이익률 |", "|---|---|---|---|"]
        for q in m["quarters"]:
            om = q["op_income"] / q["revenue"] if q["op_income"] is not None and q["revenue"] else None
            L.append(f"| {q['end']} | {money(q['revenue'])} | {money(q['op_income'])} | {pct(om)} |")
        L.append("")
    L += ["## 검증용 원본 수치", "", "아래 숫자를 링크의 원본 공시와 대조해 보세요.", "",
          "| 항목 | 값 | 원본 (공시 / 기간) |", "|---|---|---|"]
    for label, val, src in m["sources"]:
        fmt = (lambda v: f"{v:,.0f}주") if "주식" in label else money
        refs = "<br>".join(
            f"[{s['form']} {s.get('fy', '')}{s.get('fp', '')}]({link(m['cik'], s['accn'])}) "
            f"{s['start'] + ' ~ ' if s.get('start') else ''}{s['end']}: {fmt(s['val'])}"
            f"{' `' + s['tag'] + '`' if s.get('tag') else ''}"
            for s in src)
        L.append(f"| {label} | {fmt(val)} | {refs} |")
    if any(len(src) == 3 for _, _, src in m["sources"]):
        L += ["", "원본이 3개인 항목은 `직전 연간 + 올해 누적 - 작년 같은 기간 누적`으로 계산한 값입니다."]
    return "\n".join(L)


def tickers_from_reports(kind):
    path = REPORTS / ("overlap.csv" if kind == "overlap" else "new_buys.csv")
    if not path.exists():
        sys.exit(f"{path} 가 없습니다. 먼저 `python tracker.py update`를 실행하세요.")
    with open(path, encoding="utf-8-sig") as f:
        return sorted({r["ticker"] for r in csv.DictReader(f) if r["ticker"] and " " not in r["ticker"]})


def main():
    ap = argparse.ArgumentParser(description="종목 기본 지표 조사")
    ap.add_argument("tickers", nargs="*")
    ap.add_argument("--overlap", action="store_true", help="13F 중복 매수 종목")
    ap.add_argument("--new-buys", action="store_true", help="13F 신규 매수 종목 전체")
    args = ap.parse_args()

    tickers = [t.upper() for t in args.tickers]
    if args.overlap:
        tickers += tickers_from_reports("overlap")
    if args.new_buys:
        tickers += tickers_from_reports("new_buys")
    tickers = list(dict.fromkeys(tickers))
    if not tickers:
        ap.print_help()
        return

    sec = Sec(load_config()["user_agent"])
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for tk in tickers:
        try:
            m = analyze(sec, tk)
        except (ValueError, requests.RequestException) as e:
            print(f"[건너뜀] {e}")
            continue
        (OUT / f"{m['ticker']}.md").write_text(to_markdown(m), encoding="utf-8")
        rows.append(m)
        print(f"{m['ticker']:<6} PER {num(m['per']) if m['per'] else (m['per_note'] or '-'):>7}  "
              f"PBR {num(m['pbr']):>6}  매출성장 {pct(m['revenue_growth']):>7}  영업이익률 {pct(m['op_margin']):>7}  "
              f"FCF {money(m['fcf']):>10}  부채비율 {num(m['debt_ratio'], '%'):>8}  ({m['name']})")

    if len(rows) > 1:
        L = ["# 종목 지표 비교", "", f"생성: {datetime.now():%Y-%m-%d %H:%M}", "",
             "| 티커 | 종목 | PER | PBR | 매출 성장률 | 영업이익률 | FCF | 부채비율 | 재무 기준일 |",
             "|---|---|---|---|---|---|---|---|---|"]
        for m in rows:
            L.append(f"| [{m['ticker']}](fundamentals/{m['ticker']}.md) | {m['name']} | "
                     f"{num(m['per']) if m['per'] else (m['per_note'] or '-')} | {num(m['pbr'])} | "
                     f"{pct(m['revenue_growth'])} | {pct(m['op_margin'])} | {money(m['fcf'])} | "
                     f"{num(m['debt_ratio'], '%')} | {m['period_end']} |")
        (REPORTS / "fundamentals_summary.md").write_text("\n".join(L), encoding="utf-8")
        with open(REPORTS / "fundamentals_summary.csv", "w", newline="", encoding="utf-8-sig") as f:
            keys = ["ticker", "name", "period_end", "price", "market_cap", "per", "pbr", "revenue_growth",
                    "op_margin", "fcf", "fcf_margin", "debt_ratio"]
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"\n저장: {REPORTS / 'fundamentals_summary.md'}")
    if rows:
        print(f"종목별 상세(검증용 원본 링크 포함): {OUT}")


if __name__ == "__main__":
    main()
