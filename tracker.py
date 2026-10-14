"""13F 신규 매수 추적기.

SEC EDGAR에서 선택한 투자자들의 13F 공시를 받아와
  1) 투자자별로 이번 분기에 새로 편입한 종목(신규 매수)을 뽑고
  2) 여러 투자자가 동시에 신규 매수한 종목(중복 매수)을 정리합니다.

사용법:
  python tracker.py update                  # 새 공시 확인 + 리포트 생성
  python tracker.py report [--period DATE]  # 저장된 데이터로 리포트만 다시 생성
  python tracker.py search "pershing"       # 투자자 이름으로 CIK 검색
  python tracker.py add 1336528 "Pershing Square (Bill Ackman)"
  python tracker.py remove 1336528
  python tracker.py list
"""
import argparse
import csv
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "investors.json"
DATA = BASE / "data"
FILINGS = DATA / "filings"
STATE = DATA / "state.json"
TICKERS = DATA / "tickers.json"
REPORTS = BASE / "reports"

REQUEST_INTERVAL = 0.15  # SEC 제한: 초당 10회 이하

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------- 설정/저장

def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config():
    cfg = load_json(CONFIG, None)
    if cfg is None:
        sys.exit(f"설정 파일이 없습니다: {CONFIG}")
    # GitHub Actions 등에서는 연락처를 저장소에 올리지 않고 환경변수로 넣습니다
    if os.environ.get("SEC_USER_AGENT"):
        cfg["user_agent"] = os.environ["SEC_USER_AGENT"]
    if "your-email@example.com" in cfg.get("user_agent", ""):
        print("[주의] investors.json의 user_agent에 본인 이름과 이메일을 넣어 주세요 "
              "(SEC 요청 정책). 지금은 기본값으로 진행합니다.\n")
    return cfg


# ---------------------------------------------------------------- SEC 요청

class Sec:
    def __init__(self, user_agent):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self._last = 0.0

    def get(self, url, **kw):
        wait = REQUEST_INTERVAL - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            self._last = time.time()
            r = self.s.get(url, timeout=30, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        r.raise_for_status()

    def submissions(self, cik):
        return self.get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json").json()

    def filing_files(self, cik, acc):
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/index.json"
        return [i["name"] for i in self.get(url).json()["directory"]["item"]]

    def filing_text(self, cik, acc, name):
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{name}"
        return self.get(url).content


def strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def find_text(el, path, default=""):
    x = el.find(path)
    return x.text.strip() if x is not None and x.text else default


# ---------------------------------------------------------------- 공시 파싱

def fetch_filing(sec, cik, meta):
    """공시 1건의 보유 내역을 받아 파싱합니다. 결과는 캐시되며 다시 받지 않습니다."""
    acc = meta["accession"]
    cache = FILINGS / f"{acc}.json"
    if cache.exists():
        cached = load_json(cache, None)
        if cached.get("cik") != int(cik):  # 예전 캐시에는 cik가 없음
            cached["cik"] = int(cik)
            save_json(cache, cached)
        return cached

    files = sec.filing_files(cik, acc)
    amendment_type = ""
    if "primary_doc.xml" in files:
        primary = strip_ns(ET.fromstring(sec.filing_text(cik, acc, "primary_doc.xml")))
        amendment_type = find_text(primary, ".//amendmentInfo/amendmentType").upper()

    info_name = next((f for f in files if f.lower().endswith(".xml") and f != "primary_doc.xml"), None)
    # 2023년 1월 3일 이후 제출분부터 금액 단위가 달러(이전은 천 달러)
    multiplier = 1 if meta["filing_date"] >= "2023-01-03" else 1000
    holdings = []
    if info_name:
        root = strip_ns(ET.fromstring(sec.filing_text(cik, acc, info_name)))
        for it in root.iter("infoTable"):
            holdings.append({
                "name": find_text(it, "nameOfIssuer"),
                "class": find_text(it, "titleOfClass"),
                "cusip": find_text(it, "cusip").upper(),
                "value": float(find_text(it, "value", "0") or 0) * multiplier,
                "shares": float(find_text(it, "shrsOrPrnAmt/sshPrnamt", "0") or 0),
                "share_type": find_text(it, "shrsOrPrnAmt/sshPrnamtType"),
                "put_call": find_text(it, "putCall").upper(),
            })

    result = {**meta, "amendment_type": amendment_type, "holdings": holdings}
    save_json(cache, result)
    return result


def list_13f_filings(subs):
    f = subs["filings"]["recent"]
    out = []
    for i, form in enumerate(f["form"]):
        if form in ("13F-HR", "13F-HR/A"):
            out.append({
                "accession": f["accessionNumber"][i],
                "form": form,
                "filing_date": f["filingDate"][i],
                "period": f["reportDate"][i],
            })
    return out


def value_scale(holdings):
    """일부 운용사는 규정 변경 후에도 천 달러 단위로 신고합니다.
    주당 평가액의 중앙값이 $1 미만이면 천 달러 단위로 보고 1000을 곱합니다."""
    prices = sorted(h["value"] / h["shares"] for h in holdings
                    if h["share_type"] == "SH" and h["shares"] > 0 and not h["put_call"])
    if prices and prices[len(prices) // 2] < 1.0:
        return 1000
    return 1


def build_positions(filings, include_options, include_bonds):
    """한 분기의 공시(원본 + 정정)를 합쳐 CUSIP별 보유 내역을 만듭니다.

    - 원본 13F-HR, RESTATEMENT 정정: 보유 내역 전체를 대체
    - NEW HOLDINGS 정정: 비공개 처리됐다가 나중에 공개된 종목 등을 추가

    한 투자자가 CIK 여러 개로 신고한 분기(법인 이전 등)는 CIK별로 따로 합친 뒤 더합니다.
    두 CIK가 같은 종목을 함께 신고했으면 같은 지분을 중복 신고한 것으로 보고 큰 쪽만 씁니다.
    """
    by_cik = defaultdict(list)
    for fl in filings:
        by_cik[fl.get("cik")].append(fl)

    pos = {}
    for fls in by_cik.values():
        for key, p in _positions_one_filer(fls, include_options, include_bonds).items():
            if key not in pos or p["value"] > pos[key]["value"]:
                pos[key] = p
    return pos


def _positions_one_filer(filings, include_options, include_bonds):
    lines = []
    for fl in sorted(filings, key=lambda x: (x["filing_date"], x["accession"])):
        scale = value_scale(fl["holdings"])
        scaled = [{**h, "value": h["value"] * scale} for h in fl["holdings"]]
        if fl["form"] == "13F-HR/A" and fl["amendment_type"] == "NEW HOLDINGS":
            lines += scaled
        else:
            lines = scaled

    pos = {}
    for h in lines:
        if h["put_call"] and not include_options:
            continue
        if h["share_type"] == "PRN" and not include_bonds:
            continue
        key = h["cusip"] + (f" {h['put_call']}" if h["put_call"] else "")
        p = pos.setdefault(key, {"cusip": h["cusip"], "name": h["name"], "class": h["class"],
                                 "put_call": h["put_call"], "value": 0.0, "shares": 0.0})
        p["value"] += h["value"]
        p["shares"] += h["shares"]
    return pos


# ---------------------------------------------------------------- 티커 변환 (OpenFIGI)

def resolve_tickers(cusips):
    """CUSIP -> 티커. OpenFIGI 무료 API를 쓰며, 실패하면 티커 없이 진행합니다."""
    cache = load_json(TICKERS, {})
    todo = sorted({c for c in cusips if c and c not in cache})
    for i in range(0, len(todo), 10):
        batch = todo[i:i + 10]
        try:
            r = requests.post("https://api.openfigi.com/v3/mapping", timeout=30,
                              json=[{"idType": "ID_CUSIP", "idValue": c} for c in batch])
            if r.status_code == 429:
                time.sleep(60)
                r = requests.post("https://api.openfigi.com/v3/mapping", timeout=30,
                                  json=[{"idType": "ID_CUSIP", "idValue": c} for c in batch])
            r.raise_for_status()
            for c, res in zip(batch, r.json()):
                data = res.get("data") or []
                us = [d for d in data if d.get("exchCode") == "US"] or data
                cache[c] = us[0].get("ticker", "") if us else ""
        except requests.RequestException as e:
            print(f"  (티커 조회 실패, 건너뜀: {e})")
            break
        time.sleep(2.5)  # API 키 없이 분당 25회 제한
    save_json(TICKERS, cache)
    return cache


# ---------------------------------------------------------------- 명령: update

def cmd_update(args):
    cfg = load_config()
    sec = Sec(cfg["user_agent"])
    history = max(2, int(cfg.get("history_quarters", 5)))  # 투자자별로 최근 몇 분기를 가져올지
    state = load_json(STATE, {"seen": [], "investors": {}})
    seen = set(state["seen"])
    newly_found = []

    for inv in cfg["investors"]:
        cik, name = int(inv["cik"]), inv["name"]
        # prior_ciks: 법인을 옮겨 예전 공시가 다른 CIK에 있는 경우 (예: 애크먼)
        ciks = [cik] + [int(c) for c in inv.get("prior_ciks", [])]
        print(f"- {name} (CIK {', '.join(map(str, ciks))})")
        try:
            filings = [{**m, "cik": c} for c in ciks for m in list_13f_filings(sec.submissions(c))]
        except requests.RequestException as e:
            print(f"  불러오기 실패: {e}")
            continue
        periods = sorted({f["period"] for f in filings}, reverse=True)[:history]
        parsed = []
        for meta in filings:
            if meta["period"] not in periods:
                continue
            try:
                parsed.append(fetch_filing(sec, meta["cik"], meta))
            except (requests.RequestException, ET.ParseError) as e:
                print(f"  공시 {meta['accession']} 파싱 실패: {e}")
                continue
            if meta["accession"] not in seen:
                seen.add(meta["accession"])
                newly_found.append((name, meta))
        state["investors"][str(cik)] = {
            "name": name,
            "filings": [{k: v for k, v in p.items() if k != "holdings"} for p in parsed],
        }
        print(f"  최근 분기: {periods[0] if periods else '없음'}, 공시 {len(parsed)}건")

    first_run = not state["seen"]
    state["seen"] = sorted(seen)
    state["last_update"] = datetime.now().isoformat(timespec="seconds")
    save_json(STATE, state)

    print()
    if first_run:
        print(f"첫 실행: 공시 {len(newly_found)}건을 받았습니다.")
    elif newly_found:
        print("*** 새 13F 공시가 나왔습니다 ***")
        for name, m in newly_found:
            print(f"  {name}: {m['form']} (분기 {m['period']}, 제출 {m['filing_date']})")
    else:
        print("새 공시가 없습니다.")
    print()
    cmd_report(args, cfg)


# ---------------------------------------------------------------- 명령: report

def quarter_label(period):
    y, m, _ = period.split("-")
    return f"{y} Q{(int(m) - 1) // 3 + 1}"


def fmt_money(v):
    if v >= 1e9:
        return f"${v / 1e9:,.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:,.1f}M"
    return f"${v:,.0f}"


def cmd_report(args, cfg=None):
    cfg = cfg or load_config()
    state = load_json(STATE, None)
    if not state:
        sys.exit("데이터가 없습니다. 먼저 `python tracker.py update`를 실행하세요.")
    include_options = cfg.get("include_options", False)
    include_bonds = cfg.get("include_bonds", False)
    window = max(1, int(cfg.get("quarters_window", 1)))
    min_overlap = max(2, int(cfg.get("min_overlap", 2)))

    # 투자자별 분기별 보유 내역
    per_inv = {}
    for inv in cfg["investors"]:
        st = state["investors"].get(str(int(inv["cik"])))
        if not st:
            continue
        by_period = defaultdict(list)
        for meta in st["filings"]:
            fl = load_json(FILINGS / f"{meta['accession']}.json", None)
            if fl:
                by_period[fl["period"]].append(fl)
        per_inv[inv["name"]] = {
            p: {"positions": build_positions(fls, include_options, include_bonds),
                "filed": max(f["filing_date"] for f in fls)}
            for p, fls in by_period.items()
        }

    all_periods = sorted({p for d in per_inv.values() for p in d}, reverse=True)
    if not all_periods:
        sys.exit("분석할 공시가 없습니다.")
    target = args.period if getattr(args, "period", None) else all_periods[0]
    target_periods = [p for p in all_periods if p <= target][:window]

    # 신규 매수 = 해당 분기에 있고 직전 분기에는 없던 종목
    new_buys = {}  # name -> list of dict
    for name, periods in per_inv.items():
        ordered = sorted(periods)
        buys = []
        for p in target_periods:
            if p not in periods:
                continue
            idx = ordered.index(p)
            if idx == 0:
                continue  # 비교할 직전 분기 없음
            prev = periods[ordered[idx - 1]]["positions"]
            for key, pos in periods[p]["positions"].items():
                if key not in prev:
                    buys.append({**pos, "period": p, "filed": periods[p]["filed"]})
        new_buys[name] = sorted(buys, key=lambda x: -x["value"])

    tickers = resolve_tickers([b["cusip"] for bs in new_buys.values() for b in bs])

    # 중복 매수
    overlap = {}
    for name, buys in new_buys.items():
        for b in buys:
            o = overlap.setdefault((b["cusip"], b["put_call"]),
                                   {"cusip": b["cusip"], "put_call": b["put_call"], "name": b["name"],
                                    "investors": [], "value": 0.0})
            if name not in o["investors"]:
                o["investors"].append(name)
            o["value"] += b["value"]
    overlaps = sorted((o for o in overlap.values() if len(o["investors"]) >= min_overlap),
                      key=lambda o: (-len(o["investors"]), -o["value"]))

    # ---- 출력
    span = ", ".join(quarter_label(p) for p in sorted(target_periods))
    lines = [f"# 13F 신규 매수 리포트 ({span})", "",
             f"생성: {datetime.now():%Y-%m-%d %H:%M}  |  마지막 업데이트: {state.get('last_update', '-')}", ""]

    lines += [f"## 중복 신규 매수 ({min_overlap}명 이상)", ""]
    if overlaps:
        lines += ["| 티커 | 종목 | 매수 투자자 수 | 투자자 | 합계 평가액 |", "|---|---|---|---|---|"]
        for o in overlaps:
            label = o["name"] + (f" ({o['put_call']})" if o["put_call"] else "")
            lines.append(f"| {tickers.get(o['cusip'], '') or '-'} | {label} | {len(o['investors'])} | "
                         f"{', '.join(o['investors'])} | {fmt_money(o['value'])} |")
    else:
        lines.append("해당 기간에 여러 투자자가 함께 신규 매수한 종목이 없습니다.")
    lines.append("")

    lines += ["## 투자자별 신규 매수", ""]
    for inv in cfg["investors"]:
        name = inv["name"]
        buys = new_buys.get(name)
        periods = per_inv.get(name, {})
        latest = max(periods) if periods else None
        note = ""
        if latest and latest < target_periods[0]:
            note = f" — 아직 {quarter_label(target_periods[0])} 공시 전 (최근: {quarter_label(latest)})"
        lines.append(f"### {name}{note}")
        if buys:
            lines += ["| 분기 | 티커 | 종목 | 주식 수 | 평가액 |", "|---|---|---|---|---|"]
            for b in buys:
                label = b["name"] + (f" ({b['put_call']})" if b["put_call"] else "")
                lines.append(f"| {quarter_label(b['period'])} | {tickers.get(b['cusip'], '') or '-'} | {label} | "
                             f"{b['shares']:,.0f} | {fmt_money(b['value'])} |")
        else:
            lines.append("신규 매수 없음" if periods else "데이터 없음")
        lines.append("")

    text = "\n".join(lines)
    print(text)

    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "latest.md").write_text(text, encoding="utf-8")
    with open(REPORTS / "new_buys.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["investor", "period", "filed", "ticker", "name", "class", "cusip", "put_call", "shares", "value_usd"])
        for name, buys in new_buys.items():
            for b in buys:
                w.writerow([name, b["period"], b["filed"], tickers.get(b["cusip"], ""), b["name"], b["class"],
                            b["cusip"], b["put_call"], int(b["shares"]), int(b["value"])])
    with open(REPORTS / "overlap.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "name", "cusip", "put_call", "investor_count", "investors", "total_value_usd"])
        for o in overlaps:
            w.writerow([tickers.get(o["cusip"], ""), o["name"], o["cusip"], o["put_call"],
                        len(o["investors"]), "; ".join(o["investors"]), int(o["value"])])
    print(f"\n저장: {REPORTS / 'latest.md'}, new_buys.csv, overlap.csv")


# ---------------------------------------------------------------- 명령: 투자자 관리

def cmd_search(args):
    cfg = load_config()
    sec = Sec(cfg["user_agent"])
    hits = sec.get("https://efts.sec.gov/LATEST/search-index", params={"keysTyped": args.query}).json()
    hits = hits.get("hits", {}).get("hits", [])[:10]
    if not hits:
        print("검색 결과가 없습니다.")
        return
    print(f"{'CIK':>10}  {'최근 13F':<24}  이름")
    for h in hits:
        cik = int(h["_id"])
        try:
            filings = list_13f_filings(sec.submissions(cik))
            last = f"{filings[0]['period']} ({filings[0]['form']})" if filings else "13F 없음"
        except requests.RequestException:
            last = "?"
        print(f"{cik:>10}  {last:<24}  {h['_source'].get('entity', '')}")
    print("\n추가하려면: python tracker.py add <CIK> \"표시할 이름\"")


def cmd_add(args):
    cfg = load_config()
    cik = int(args.cik)
    if any(int(i["cik"]) == cik for i in cfg["investors"]):
        print("이미 목록에 있습니다.")
        return
    name = args.name
    if not name:
        name = Sec(cfg["user_agent"]).submissions(cik)["name"]
    cfg["investors"].append({"name": name, "cik": cik})
    save_json(CONFIG, cfg)
    print(f"추가됨: {name} (CIK {cik})")


def cmd_remove(args):
    cfg = load_config()
    before = len(cfg["investors"])
    cfg["investors"] = [i for i in cfg["investors"] if int(i["cik"]) != int(args.cik)]
    save_json(CONFIG, cfg)
    print("삭제됨" if len(cfg["investors"]) < before else "목록에 없는 CIK입니다.")


def cmd_list(args):
    for i in load_config()["investors"]:
        print(f"{i['cik']:>10}  {i['name']}")


def main():
    ap = argparse.ArgumentParser(description="13F 신규 매수 추적기")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("update", help="새 공시 확인 후 리포트 생성")
    p.add_argument("--period", help="기준 분기 말일 (예: 2026-06-30). 기본은 최신 분기")
    p.set_defaults(func=cmd_update)
    p = sub.add_parser("report", help="저장된 데이터로 리포트 생성")
    p.add_argument("--period")
    p.set_defaults(func=cmd_report)
    p = sub.add_parser("search", help="투자자 이름으로 CIK 검색")
    p.add_argument("query")
    p.set_defaults(func=cmd_search)
    p = sub.add_parser("add", help="투자자 추가")
    p.add_argument("cik")
    p.add_argument("name", nargs="?")
    p.set_defaults(func=cmd_add)
    p = sub.add_parser("remove", help="투자자 삭제")
    p.add_argument("cik")
    p.set_defaults(func=cmd_remove)
    sub.add_parser("list", help="투자자 목록").set_defaults(func=cmd_list)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
