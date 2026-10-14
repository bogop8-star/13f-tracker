"""tracker.py가 받아 둔 13F 데이터를 웹 화면용 JSON으로 내보냅니다.

사용법:
  python export.py            # app/public/data/holdings.json 생성

투자자마다 최신 분기를 기준으로
  - 보유 종목 상위 TOP_HOLDINGS개 (비중, 직전 분기 주식 수 포함)
  - 신규 매수 (직전 분기에 없던 종목). 퀀트/멀티전략 펀드는 평가액 상위 QUANT_NEW_BUYS개만
  - 전량 매도 (직전 분기에 있었고 이번 분기에 없는 종목). 직전 분기 평가액 상위 SOLD_LIMIT개
를 담습니다. 중복 보유·중복 신규 매수는 선택한 투자자 조합에 따라 화면에서 계산합니다.
"""
import re
from collections import defaultdict
from datetime import datetime

from tracker import (BASE, FILINGS, STATE, build_positions, load_config, load_json,
                     resolve_tickers, save_json)

OUT = BASE / "app" / "public" / "data" / "holdings.json"
TOP_HOLDINGS = 100   # 중복 보유 계산에도 이 범위만 씁니다
QUANT_NEW_BUYS = 5   # 퀀트/멀티전략 펀드는 신규 매수·전량 매도를 상위 5개만
SOLD_LIMIT = 20


def filing_url(cik, acc):
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{acc}-index.htm"


def split_name(name):
    """'Berkshire Hathaway (Warren Buffett)' -> ('Berkshire Hathaway', 'Warren Buffett')"""
    m = re.match(r"(.+?)\s*\((.+)\)$", name)
    return (m.group(1), m.group(2)) if m else (name, name)


def main():
    cfg = load_config()
    state = load_json(STATE, None)
    if not state:
        raise SystemExit("데이터가 없습니다. 먼저 `python tracker.py update`를 실행하세요.")
    include_options = cfg.get("include_options", False)
    include_bonds = cfg.get("include_bonds", False)

    rows = []
    for inv in cfg["investors"]:
        cik = int(inv["cik"])
        st = state["investors"].get(str(cik))
        if not st:
            print(f"- {inv['name']}: 받은 데이터 없음, 건너뜀")
            continue
        by_period = defaultdict(list)
        for meta in st["filings"]:
            fl = load_json(FILINGS / f"{meta['accession']}.json", None)
            if fl:
                by_period[fl["period"]].append(fl)
        if not by_period:
            continue
        periods = sorted(by_period)
        latest, prev = periods[-1], (periods[-2] if len(periods) > 1 else None)
        fls = by_period[latest]
        pos = build_positions(fls, include_options, include_bonds)
        prev_pos = build_positions(by_period[prev], include_options, include_bonds) if prev else None
        original = min((f for f in fls if f["form"] == "13F-HR"), key=lambda f: f["filing_date"], default=fls[0])

        total = sum(p["value"] for p in pos.values())
        ranked = sorted(pos.items(), key=lambda kv: -kv[1]["value"])
        quant = inv.get("style") == "quant"
        new = [(k, p) for k, p in ranked if prev_pos is not None and k not in prev_pos]
        # 전량 매도 = 직전 분기에 있었고 이번 분기에는 없는 종목 (직전 분기 평가액 순)
        sold = sorted(((k, p) for k, p in (prev_pos or {}).items() if k not in pos), key=lambda kv: -kv[1]["value"])

        def row(k, p):
            return {
                "cusip": p["cusip"], "name": p["name"], "cls": p["class"],
                "value": round(p["value"]), "weight": p["value"] / total if total else 0,
                "shares": p["shares"],
                "prevShares": prev_pos[k]["shares"] if prev_pos and k in prev_pos else None,
            }

        firm, person = split_name(inv["name"])
        rows.append({
            "cik": cik, "firm": firm, "person": person, "ko": inv.get("ko", person),
            "style": inv.get("style"),
            "period": latest, "prevPeriod": prev,
            "filed": max(f["filing_date"] for f in fls),
            "accession": original["accession"],
            "url": filing_url(original.get("cik", cik), original["accession"]),
            "totalValue": round(total), "positionCount": len(pos),
            "holdings": [row(k, p) for k, p in ranked[:TOP_HOLDINGS]],
            "newBuyCount": len(new) if prev_pos is not None else None,
            "newBuys": [row(k, p) for k, p in (new[:QUANT_NEW_BUYS] if quant else new)],
            "soldCount": len(sold) if prev_pos is not None else None,
            "soldOut": [{"cusip": p["cusip"], "name": p["name"], "prevValue": round(p["value"])}
                        for _, p in sold[:QUANT_NEW_BUYS if quant else SOLD_LIMIT]],
        })
        print(f"- {inv['name']}: {latest} 보유 {len(pos)}개, 신규 {len(new)}개")

    tickers = resolve_tickers({h["cusip"] for r in rows for h in r["holdings"] + r["newBuys"] + r["soldOut"]})
    for r in rows:
        for h in r["holdings"] + r["newBuys"] + r["soldOut"]:
            h["ticker"] = tickers.get(h["cusip"], "")
        # 주식(SH)으로 신고됐지만 티커가 'GOOGL 6.25 05/15/29'처럼 나오는 것은 회사채라서 목록에서 뺍니다
        for key in ("holdings", "newBuys", "soldOut"):
            r[key] = [h for h in r[key] if " " not in h["ticker"]]

    save_json(OUT, {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "lastUpdate": state.get("last_update"),
        "topHoldings": TOP_HOLDINGS, "quantNewBuys": QUANT_NEW_BUYS,
        "investors": rows,
    })
    print(f"\n저장: {OUT} (투자자 {len(rows)}명)")


if __name__ == "__main__":
    main()
