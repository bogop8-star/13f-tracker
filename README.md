# 13F 신규 매수 추적기

SEC EDGAR에서 고른 투자자들의 13F 공시를 받아서 아래 두 가지를 정리합니다.
- 투자자별로 이번 분기에 **새로 편입한 종목**
- **여러 투자자가 함께 신규 매수한 종목**

## 처음 설정

1. `investors.json`의 `user_agent`에 본인 이름과 이메일을 넣습니다. SEC는 요청마다 연락처를 요구합니다.
2. 투자자를 관리합니다.

```
python tracker.py search "pershing"          # 이름으로 CIK 검색 (최근 13F 제출 여부도 표시)
python tracker.py add 2026053 "Pershing Square (Bill Ackman)"
python tracker.py remove 1709323
python tracker.py list
```

## 실행

```
python tracker.py update                     # 새 공시 확인 + 리포트 생성
python tracker.py report --period 2026-03-31 # 지난 분기 기준 리포트
```

결과 파일:
- `reports/latest.md`: 중복 매수 표와 투자자별 신규 매수 표
- `reports/new_buys.csv`, `reports/overlap.csv`: 엑셀로 열 수 있는 원본 데이터

## 설정 (`investors.json`)

| 키 | 의미 |
|---|---|
| `min_overlap` | 몇 명 이상이 샀을 때 중복 매수로 볼지 (기본 2) |
| `quarters_window` | 최근 몇 분기의 신규 매수를 합쳐서 볼지. 2로 두면 "A는 1분기, B는 2분기에 새로 산 종목"도 중복으로 잡힙니다 |
| `history_quarters` | 투자자별로 받아 둘 분기 수 (기본 5) |
| `include_options` | 콜/풋 옵션 포함 여부 (기본 false) |
| `include_bonds` | 전환사채 같은 채권 포함 여부 (기본 false) |

## 판정 방법

- **신규 매수**: 해당 분기 보유 목록에는 있고 직전 분기 목록에는 없는 종목(CUSIP 기준)
- 정정공시 중 **NEW HOLDINGS**는 원본에 더합니다. 버크셔처럼 비공개로 샀다가 나중에 공개한 종목도 원래 분기의 신규 매수로 잡힙니다. **RESTATEMENT**는 원본을 대체합니다.
- 금액을 아직 천 달러 단위로 신고하는 운용사가 있어서, 주당 평가액의 중앙값이 $1 미만이면 1000을 곱해 보정합니다.
- 티커는 OpenFIGI 무료 API로 CUSIP에서 변환합니다. 변환이 안 되면 `-`로 표시됩니다.

## 종목 지표 조사 (`fundamentals.py`)

```
python fundamentals.py AAPL MSFT    # 종목 지정
python fundamentals.py --overlap    # 13F 중복 매수 종목
python fundamentals.py --new-buys   # 13F 신규 매수 종목 전체
```

SEC 재무 데이터(XBRL)와 Yahoo 현재 주가로 계산합니다. 손익과 현금흐름은 최근 12개월 기준입니다.

| 지표 | 계산 | 왜 보는가 |
|---|---|---|
| PER | 시가총액 ÷ 순이익 | 이익 대비 주가가 비싼지 싼지 |
| PBR | 시가총액 ÷ 자기자본 | 장부상 순자산 대비 주가 수준 |
| 매출 성장률 | 최근 12개월 매출 ÷ 1년 전 - 1 | 사업이 커지고 있는지 |
| 영업이익률 | 영업이익 ÷ 매출 | 본업으로 얼마나 남기는지(경쟁력) |
| 잉여현금흐름 | 영업현금흐름 - 설비투자 | 회계 이익이 실제 현금으로 들어오는지 |
| 부채비율 | 총부채 ÷ 자기자본 × 100 | 재무적으로 버틸 힘이 있는지 |

결과:
- `reports/fundamentals/<티커>.md`: 지표, 분기별 매출·영업이익률, **검증용 원본 수치(공시 링크)**
- `reports/fundamentals_summary.md` / `.csv`: 여러 종목 비교표

한계:
- 미국 회계기준(US-GAAP)으로 공시하는 기업만 됩니다. IFRS를 쓰는 외국 기업과 ETF는 건너뜁니다.
- 은행, 보험, 건설사는 영업이익 항목이 없어서 영업이익률이 비고, 부채비율도 업종 특성상 높게 나옵니다.
- 주식 종류가 여러 개이고 종류마다 주가가 크게 다르면(예: 버크셔 A/B주) 시가총액, PER, PBR이 틀릴 수 있습니다.

## 자동 실행 (Windows 작업 스케줄러)

13F는 분기가 끝나고 45일 안에 나옵니다(2월, 5월, 8월, 11월 중순). 매일 한 번 돌려 두면 새 공시가 나왔을 때 `update.log`에 `*** 새 13F 공시가 나왔습니다 ***`가 찍힙니다.

```
schtasks /Create /TN "13F Tracker" /SC DAILY /ST 09:00 /TR "\"C:\Users\N-\Desktop\aiffel_work\13f_tracker\run_update.bat\""
```
