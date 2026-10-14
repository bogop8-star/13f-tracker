export function money(v: number): string {
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`
  if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}K`
  return `$${v.toFixed(0)}`
}

export function pct(v: number): string {
  if (v > 0 && v < 0.0001) return '<0.01%'
  return `${(v * 100).toFixed(v >= 0.1 ? 1 : 2)}%`
}

/** '2026-06-30' -> '2026 Q2' */
export function quarter(period: string): string {
  const [y, m] = period.split('-')
  return `${y} Q${Math.floor((Number(m) - 1) / 3) + 1}`
}

/** 직전 분기 대비 주식 수 변화 */
export function shareChange(shares: number, prev: number | null): { label: string; kind: 'new' | 'up' | 'down' | 'same' } {
  if (prev === null) return { label: '신규', kind: 'new' }
  if (prev === 0) return { label: '-', kind: 'same' }
  const r = shares / prev - 1
  if (Math.abs(r) < 0.005) return { label: '유지', kind: 'same' }
  return { label: `${r > 0 ? '+' : ''}${(r * 100).toFixed(0)}%`, kind: r > 0 ? 'up' : 'down' }
}
