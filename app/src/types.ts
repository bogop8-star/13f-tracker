// export.py가 만드는 app/public/data/holdings.json의 구조

export interface Position {
  cusip: string
  ticker: string
  name: string
  cls: string
  value: number
  weight: number
  shares: number
  /** 직전 분기 주식 수. 직전 분기에 없었으면 null */
  prevShares: number | null
}

export interface Investor {
  cik: number
  firm: string
  person: string
  ko: string
  style: 'quant' | null
  period: string
  prevPeriod: string | null
  filed: string
  accession: string
  url: string
  totalValue: number
  positionCount: number
  /** 평가액 상위 topHoldings개 */
  holdings: Position[]
  /** 직전 분기가 없어 판정할 수 없으면 null */
  newBuyCount: number | null
  /** 퀀트/멀티전략 펀드는 평가액 상위 quantNewBuys개만 */
  newBuys: Position[]
}

export interface HoldingsData {
  generatedAt: string
  lastUpdate: string | null
  topHoldings: number
  quantNewBuys: number
  investors: Investor[]
}
