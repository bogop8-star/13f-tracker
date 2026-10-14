"""holdings.json을 RAG용 사실 단위 청크로 바꿉니다.

사용법:
  python build_chunks.py      # app/public/data/chunks.json 생성 (vector 없음)
  (이후 app에서 npm run embed 로 vector를 붙여 docs.json을 만듭니다)

청크 스키마: id, text, url, section
  - 투자자별: 포트폴리오 개요 / 신규 매수 / 주식 수 변화·전량 매도
  - 투자자 16명 전체: 중복 신규 매수 / 중복 보유
  - 13F 제도 설명: SEC Form 13F FAQ, 이 서비스의 판정 기준
ID는 SEC-001부터 위 순서대로 붙입니다. 데이터가 갱신되면 ID가 가리키는 내용도 바뀌므로,
실험 기록에는 ID와 함께 기준 분기를 남깁니다.
"""
from tracker import BASE, load_json, save_json

SRC = BASE / "app" / "public" / "data" / "holdings.json"
OUT = BASE / "app" / "public" / "data" / "chunks.json"
SEC_FAQ = ("https://www.sec.gov/rules-regulations/staff-guidance/division-investment-management-"
           "frequently-asked-questions/frequently-asked-questions-about-form-13f")
REPO_PRD = "https://github.com/bogop8-star/13f-tracker/blob/main/PRD.md"
MIN_LEN = 120
NEW_BUYS_IN_CHUNK = 15  # 신규 매수가 많은 투자자도 청크 하나가 한 가지 사실에 머물도록


def money(v):
    for unit, d in (("조 달러", 1e12), ("억 달러", 1e8), ("만 달러", 1e4)):
        if v >= d:
            return f"{v / d:,.1f}{unit}"
    return f"{v:,.0f}달러"


def quarter(period):
    y, m, _ = period.split("-")
    return f"{y}년 {(int(m) - 1) // 3 + 1}분기"


def pct(w):
    return "0.01% 미만" if 0 < w < 0.0001 else f"{w * 100:.2f}%"


def stock(p):
    return f"{p['ticker']}({p['name']})" if p.get("ticker") else p["name"]


def who(inv):
    return f"{inv['ko']}({inv['person']}, {inv['firm']})"


def investor_chunks(inv):
    q, base = quarter(inv["period"]), f"{inv['ko']} · {quarter(inv['period'])}"
    quant = " 이 운용사는 퀀트/멀티전략 펀드로 종목 수가 많고 단기 매매 비중이 큽니다." if inv["style"] == "quant" else ""
    top = ", ".join(f"{i}위 {stock(p)} 비중 {p['weight'] * 100:.1f}%"
                    for i, p in enumerate(inv["holdings"][:10], 1))
    yield {
        "section": f"{base} · 포트폴리오 개요",
        "text": (f"{who(inv)}의 {q} 말 기준 13F(제출일 {inv['filed']})에 신고된 미국 상장 주식 평가액은 "
                 f"{money(inv['totalValue'])}, 보유 종목은 {inv['positionCount']:,}개입니다.{quant} "
                 f"평가액 기준 최대 보유 종목은 {top}입니다."),
    }

    if inv["newBuyCount"] is None:
        text = f"{who(inv)}는 직전 분기 13F가 없어 {q} 신규 매수를 판정할 수 없습니다."
    elif inv["newBuyCount"] == 0:
        text = (f"{who(inv)}의 {q} 13F에는 직전 분기({quarter(inv['prevPeriod'])}) 대비 새로 편입한 종목이 없습니다. "
                f"최신 분기 보유 종목 {inv['positionCount']}개가 모두 직전 분기에도 보유하던 종목이며, "
                f"이번 분기에는 기존 종목의 주식 수만 조정했습니다.")
    else:
        shown = inv["newBuys"][:NEW_BUYS_IN_CHUNK]
        lst = ", ".join(f"{stock(p)} 평가액 {money(p['value'])}(비중 {pct(p['weight'])})" for p in shown)
        if inv["style"] == "quant" and inv["newBuyCount"] > len(shown):
            limit = f" 퀀트/멀티전략 펀드라서 평가액 상위 {len(shown)}개만 정리했습니다."
        elif inv["newBuyCount"] > len(shown):
            limit = f" 아래는 평가액 상위 {len(shown)}개입니다."
        else:
            limit = ""
        text = (f"{who(inv)}가 {q}에 새로 매수한 종목, 즉 직전 분기({quarter(inv['prevPeriod'])}) 13F에 없다가 "
                f"새로 편입한 종목은 {inv['newBuyCount']}개입니다.{limit} 신규 매수 종목: {lst}.")
    yield {"section": f"{base} · 신규 매수", "text": text}

    if inv["prevPeriod"]:
        held = [p for p in inv["holdings"][:30] if p["prevShares"]]
        chg = sorted(((p["shares"] / p["prevShares"] - 1, p) for p in held), key=lambda x: x[0])
        up = [f"{stock(p)} {r * 100:+.0f}%" for r, p in reversed(chg) if r >= 0.1][:5]
        down = [f"{stock(p)} {r * 100:+.0f}%" for r, p in chg if r <= -0.1][:5]
        sold = ", ".join(f"{stock(p)}(직전 분기 평가액 {money(p['prevValue'])})" for p in inv["soldOut"][:10])
        text = (f"{who(inv)}의 {q} 13F를 직전 분기({quarter(inv['prevPeriod'])})와 비교한 주식 수 변화입니다. "
                f"상위 30개 보유 종목 중 주식 수를 10% 이상 늘린 종목: {', '.join(up) or '없음'}. "
                f"10% 이상 줄인 종목: {', '.join(down) or '없음'}. "
                f"전량 매도(직전 분기에 있었고 이번 분기에 없는 종목)는 {inv['soldCount']}개"
                + (f"이며 직전 분기 평가액이 큰 종목은 {sold}입니다." if sold else "입니다."))
        yield {"section": f"{base} · 주식 수 변화와 전량 매도", "text": text}


def cross_chunks(investors, pick, label, verb, limit):
    rows = {}
    for inv in investors:
        for p in pick(inv):
            r = rows.setdefault(p["cusip"], {"p": p, "holders": []})
            r["holders"].append((inv, p))
    multi = sorted((r for r in rows.values() if len(r["holders"]) >= 2),
                   key=lambda r: (-len(r["holders"]), -sum(p["value"] for _, p in r["holders"])))
    for r in multi[:limit]:
        hs = ", ".join(f"{inv['ko']}({inv['firm']}, 비중 {pct(p['weight'])})" for inv, p in r["holders"])
        periods = sorted({quarter(inv["period"]) for inv, _ in r["holders"]})
        yield {
            "section": f"투자자 16명 · {label}",
            "text": (f"{stock(r['p'])}는 이 서비스가 추적하는 투자자 16명의 {', '.join(periods)} 13F 기준으로 "
                     f"{len(r['holders'])}명이 {verb} 종목입니다. {verb.replace('함께 ', '')} 투자자: {hs}."),
            "url": r["holders"][0][0]["url"],
        }


CONCEPTS = [
    ("13F 제도 · 제출 대상",
     "Form 13F는 SEC가 정한 Section 13(f) 증권에 대해 1억 달러(100 million dollars) 이상의 투자 재량권을 가진 "
     "기관투자 운용사가 제출하는 분기 보유 보고서입니다. SEC에 등록된 투자자문사가 아니어도 이 기준에 해당하면 제출해야 합니다. "
     "Section 13(f) 증권은 SEC가 공개하는 Official List of Section 13(f) Securities에 오른 주식 등 지분 증권입니다.", SEC_FAQ),
    ("13F 제도 · 제출 기한과 지연",
     "13F는 각 분기가 끝난 뒤 45일 이내에 제출해야 하며, 마감일이 주말이나 공휴일이면 다음 영업일로 미뤄집니다. "
     "따라서 13F에 나온 보유 내역은 분기 말 시점의 포지션이고, 공개 시점에는 최대 45일 정도 지난 정보입니다. "
     "이 서비스의 데이터도 실시간 보유 현황이 아니라 가장 최근 분기 말 기준입니다.", SEC_FAQ),
    ("13F 제도 · 신고하지 않는 것",
     "13F에는 공매도(숏) 포지션을 신고하지 않으며, 같은 종목의 숏 포지션을 롱 포지션에서 빼서 신고하지도 않습니다. "
     "Official List에 없는 증권과 미국 밖 거래소에서만 거래되는 증권도 신고 대상이 아닙니다. "
     "그래서 13F만으로는 운용사의 전체 포트폴리오, 헤지 여부, 현금 비중을 알 수 없습니다.", SEC_FAQ),
    ("13F 제도 · 정정공시와 비공개 처리",
     "13F 정정공시(13F-HR/A)에는 이전 공시의 오류를 고쳐 전체를 다시 내는 RESTATEMENT와, 원래 공시에 새 보유 종목을 추가하는 "
     "NEW HOLDINGS가 있습니다. 운용사는 일부 보유 종목에 대해 비공개(confidential treatment)를 신청할 수 있고, "
     "비공개가 거절되거나 만료되면 NEW HOLDINGS 정정공시로 해당 종목을 공개합니다.", SEC_FAQ),
    ("이 서비스 · 판정 기준",
     "이 서비스에서 신규 매수는 최신 분기 13F에는 있고 직전 분기 13F에는 없던 종목(CUSIP 기준)을 뜻합니다. "
     "전량 매도는 직전 분기에 있었고 최신 분기에 없는 종목입니다. 콜·풋 옵션과 전환사채 같은 채권은 제외합니다. "
     "정정공시 중 NEW HOLDINGS는 원본에 더하고 RESTATEMENT는 원본을 대체합니다. 중복 보유는 투자자별 평가액 상위 100개 종목 안에서 계산하며, "
     "퀀트/멀티전략 펀드(시타델, 밀레니엄, D. E. Shaw)는 신규 매수를 평가액 상위 5개만 비교합니다. 종목 추천이나 투자 권유가 아닙니다.", REPO_PRD),
]


def main():
    data = load_json(SRC, None)
    if not data:
        raise SystemExit("holdings.json이 없습니다. 먼저 python export.py를 실행하세요.")
    invs = data["investors"]
    raw = []
    for inv in invs:
        raw += [{**c, "url": inv["url"]} for c in investor_chunks(inv)]
    raw += cross_chunks(invs, lambda i: i["newBuys"], "중복 신규 매수", "함께 새로 매수한", 12)
    raw += cross_chunks(invs, lambda i: i["holdings"], "중복 보유", "함께 보유한", 12)
    raw += [{"section": s, "text": t, "url": u} for s, t, u in CONCEPTS]

    chunks = [{"id": f"SEC-{i:03d}", "text": c["text"], "url": c["url"], "section": c["section"]}
              for i, c in enumerate(raw, 1)]
    short = [c["id"] for c in chunks if len(c["text"]) < MIN_LEN]
    if short:
        raise SystemExit(f"{MIN_LEN}자보다 짧은 청크가 있습니다: {short}")
    save_json(OUT, chunks)
    print(f"저장: {OUT} (청크 {len(chunks)}개, 평균 {sum(len(c['text']) for c in chunks) // len(chunks)}자)")


if __name__ == "__main__":
    main()
