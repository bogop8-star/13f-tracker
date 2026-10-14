import type { Investor, Position } from './types'

export interface OverlapRow {
  cusip: string
  ticker: string
  name: string
  total: number
  holders: { investor: Investor; position: Position }[]
}

/** 선택한 투자자 중 2명 이상이 함께 가진 종목. pick으로 보유 목록 또는 신규 매수 목록을 고릅니다. */
export function findOverlap(investors: Investor[], pick: (inv: Investor) => Position[]): OverlapRow[] {
  const byCusip = new Map<string, OverlapRow>()
  for (const investor of investors) {
    for (const position of pick(investor)) {
      const row = byCusip.get(position.cusip) ?? {
        cusip: position.cusip, ticker: position.ticker, name: position.name, total: 0, holders: [],
      }
      if (row.holders.some((h) => h.investor.cik === investor.cik)) continue
      row.holders.push({ investor, position })
      row.total += position.value
      byCusip.set(position.cusip, row)
    }
  }
  return [...byCusip.values()]
    .filter((r) => r.holders.length >= 2)
    .sort((a, b) => b.holders.length - a.holders.length || b.total - a.total)
}
