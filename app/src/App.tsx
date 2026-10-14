import { lazy, Suspense, useEffect, useMemo, useState, type ReactNode } from 'react'
import { money, pct, quarter, shareChange } from './format'
import { findOverlap, type OverlapRow } from './overlap'
import type { HoldingsData, Investor, Position } from './types'

// 챗봇(ONNX 런타임 포함)은 열 때만 내려받습니다
const Chat = lazy(() => import('./Chat'))

const MAX_SELECTED = 5
const STORAGE_KEY = '13f-selected'

type Tab = 'top' | 'new' | 'overlap' | 'overlapNew'
const TABS: { id: Tab; label: string }[] = [
  { id: 'top', label: '최대 보유 종목' },
  { id: 'new', label: '신규 매수' },
  { id: 'overlap', label: '중복 보유' },
  { id: 'overlapNew', label: '중복 신규 매수' },
]

function loadSelected(): number[] {
  try {
    const v = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')
    return Array.isArray(v) ? v.slice(0, MAX_SELECTED) : []
  } catch {
    return []
  }
}

export default function App() {
  const [data, setData] = useState<HoldingsData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<number[]>(loadSelected)
  const [tab, setTab] = useState<Tab>('top')
  const [limitHit, setLimitHit] = useState(false)
  const [view, setView] = useState<'dashboard' | 'chat'>('dashboard')
  // 챗봇은 처음 열 때 임베딩 모델(약 200MB)을 받으므로, 열기 전에는 만들지 않고 연 뒤에는 대화를 유지합니다
  const [chatOpened, setChatOpened] = useState(false)

  useEffect(() => {
    fetch('./data/holdings.json')
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json() as Promise<HoldingsData>
      })
      .then(setData)
      .catch((e) => setError(String(e)))
  }, [])

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(selected))
    } catch {
      /* 저장소를 못 쓰는 환경에서는 선택을 기억하지 않습니다 */
    }
  }, [selected])

  const chosen = useMemo(
    () => (data ? selected.map((cik) => data.investors.find((i) => i.cik === cik)).filter((i): i is Investor => !!i) : []),
    [data, selected],
  )

  function toggle(cik: number) {
    setLimitHit(false)
    setSelected((cur) => {
      if (cur.includes(cik)) return cur.filter((c) => c !== cik)
      if (cur.length >= MAX_SELECTED) {
        setLimitHit(true)
        return cur
      }
      return [...cur, cik]
    })
  }

  if (error) {
    return (
      <main className="page">
        <div className="banner error">
          데이터를 불러오지 못했습니다 ({error}). <code>python tracker.py update</code> 후 <code>python export.py</code>를 실행했는지 확인하세요.
        </div>
      </main>
    )
  }
  if (!data) return <main className="page"><p className="muted">데이터를 불러오는 중…</p></main>

  return (
    <main className="page">
      <header className="top">
        <div>
          <h1>13F 대가 추적기</h1>
          <p className="muted">
            미국 SEC 13F 공시로 대형 투자자의 보유·신규 매수·중복 종목을 확인합니다. 13F는 분기 말 기준이며 최대 45일 늦게 공개됩니다.
          </p>
        </div>
        <div className="meta">
          <span>데이터 갱신 {data.lastUpdate?.replace('T', ' ').slice(0, 16) ?? '-'}</span>
          <span className="muted">투자 권유가 아닙니다</span>
        </div>
      </header>

      <nav className="views">
        <button className={view === 'dashboard' ? 'on' : ''} onClick={() => setView('dashboard')}>대시보드</button>
        <button className={view === 'chat' ? 'on' : ''} onClick={() => { setView('chat'); setChatOpened(true) }}>공시 챗봇</button>
      </nav>

      {chatOpened && (
        <div hidden={view !== 'chat'}>
          <Suspense fallback={<p className="muted">챗봇을 불러오는 중…</p>}><Chat /></Suspense>
        </div>
      )}

      {view === 'dashboard' && <>
      <section>
        <div className="section-head">
          <h2>투자자 선택</h2>
          <span className={limitHit ? 'count warn' : 'count'}>
            {selected.length} / {MAX_SELECTED}명{limitHit && ' · 최대 5명까지 고를 수 있습니다'}
          </span>
          {selected.length > 0 && (
            <button className="link" onClick={() => { setSelected([]); setLimitHit(false) }}>선택 해제</button>
          )}
        </div>
        <div className="picker">
          {data.investors.map((inv) => {
            const on = selected.includes(inv.cik)
            const full = !on && selected.length >= MAX_SELECTED
            return (
              <button
                key={inv.cik}
                className={`card investor${on ? ' on' : ''}${full ? ' full' : ''}`}
                onClick={() => toggle(inv.cik)}
                aria-pressed={on}
              >
                <div className="row">
                  <strong>{inv.ko}</strong>
                  {inv.style === 'quant' && <span className="tag quant">퀀트/멀티전략</span>}
                </div>
                <div className="muted small">{inv.firm}</div>
                <div className="small stats">
                  <span>{quarter(inv.period)}</span>
                  <span>{money(inv.totalValue)}</span>
                  <span>{inv.positionCount.toLocaleString()}종목</span>
                </div>
              </button>
            )
          })}
        </div>
      </section>

      {chosen.length === 0 ? (
        <p className="empty">위에서 투자자를 1명 이상 고르면 보유 종목, 신규 매수, 중복 종목이 표시됩니다.</p>
      ) : (
        <section>
          <nav className="tabs" role="tablist">
            {TABS.map((t) => (
              <button key={t.id} role="tab" aria-selected={tab === t.id} className={tab === t.id ? 'on' : ''} onClick={() => setTab(t.id)}>
                {t.label}
              </button>
            ))}
          </nav>
          {tab === 'top' && <PerInvestor investors={chosen} render={(inv) => <TopHoldings inv={inv} />} />}
          {tab === 'new' && <PerInvestor investors={chosen} render={(inv) => <NewBuys inv={inv} quantLimit={data.quantNewBuys} />} />}
          {tab === 'overlap' && (
            <Overlap
              investors={chosen}
              rows={findOverlap(chosen, (i) => i.holdings)}
              note={`투자자별 평가액 상위 ${data.topHoldings}개 종목 안에서 2명 이상이 함께 보유한 종목입니다.`}
            />
          )}
          {tab === 'overlapNew' && (
            <Overlap
              investors={chosen}
              rows={findOverlap(chosen, (i) => i.newBuys)}
              note={`2명 이상이 직전 분기 대비 새로 편입한 종목입니다. 퀀트/멀티전략 펀드는 신규 매수 상위 ${data.quantNewBuys}개만 비교합니다.`}
            />
          )}
        </section>
      )}
      </>}
    </main>
  )
}

function PerInvestor({ investors, render }: { investors: Investor[]; render: (inv: Investor) => ReactNode }) {
  return (
    <div className="columns">
      {investors.map((inv) => (
        <article key={inv.cik} className="card panel">
          <InvestorHead inv={inv} />
          {render(inv)}
        </article>
      ))}
    </div>
  )
}

function InvestorHead({ inv }: { inv: Investor }) {
  return (
    <header className="panel-head">
      <div className="row">
        <strong>{inv.ko}</strong>
        {inv.style === 'quant' && <span className="tag quant">퀀트/멀티전략</span>}
      </div>
      <div className="muted small">
        {inv.firm} · {quarter(inv.period)} 기준 · 제출 {inv.filed} ·{' '}
        <a href={inv.url} target="_blank" rel="noreferrer">공시 원문</a>
      </div>
    </header>
  )
}

function TopHoldings({ inv }: { inv: Investor }) {
  const [all, setAll] = useState(false)
  const rows = all ? inv.holdings : inv.holdings.slice(0, 10)
  return (
    <>
      <PositionTable rows={rows} />
      {inv.holdings.length > 10 && (
        <button className="link" onClick={() => setAll(!all)}>
          {all ? '상위 10개만 보기' : `상위 ${inv.holdings.length}개 모두 보기`}
        </button>
      )}
    </>
  )
}

function NewBuys({ inv, quantLimit }: { inv: Investor; quantLimit: number }) {
  if (inv.newBuyCount === null) {
    return <p className="muted small">직전 분기 공시가 없어 신규 매수를 판정할 수 없습니다.</p>
  }
  if (inv.newBuyCount === 0) {
    return <p className="muted small">{quarter(inv.prevPeriod!)} 대비 새로 편입한 종목이 없습니다.</p>
  }
  return (
    <>
      <p className="muted small">
        {quarter(inv.prevPeriod!)} 대비 신규 편입 {inv.newBuyCount}개
        {inv.style === 'quant' && inv.newBuyCount > quantLimit && ` · 퀀트/멀티전략 펀드라 평가액 상위 ${quantLimit}개만 표시`}
      </p>
      <PositionTable rows={inv.newBuys} />
    </>
  )
}

function PositionTable({ rows }: { rows: Position[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>#</th><th>종목</th><th className="num">비중</th><th className="num">평가액</th><th className="num">주식 수 변화</th></tr>
        </thead>
        <tbody>
          {rows.map((p, i) => {
            const ch = shareChange(p.shares, p.prevShares)
            return (
              <tr key={p.cusip}>
                <td className="muted">{i + 1}</td>
                <td><Stock p={p} /></td>
                <td className="num">{pct(p.weight)}</td>
                <td className="num">{money(p.value)}</td>
                <td className={`num change ${ch.kind}`}>{ch.label}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function Stock({ p }: { p: Pick<Position, 'ticker' | 'name'> }) {
  return (
    <span className="stock">
      <span className="ticker">{p.ticker || '-'}</span>
      <span className="muted small">{p.name}</span>
    </span>
  )
}

function Overlap({ investors, rows, note }: { investors: Investor[]; rows: OverlapRow[]; note: string }) {
  const periods = new Set(investors.map((i) => i.period))
  return (
    <div className="card panel">
      <p className="muted small">{note}</p>
      {periods.size > 1 && (
        <div className="banner warn small">
          선택한 투자자들의 기준 분기가 다릅니다 ({investors.map((i) => `${i.ko} ${quarter(i.period)}`).join(', ')}).
        </div>
      )}
      {investors.length < 2 ? (
        <p className="empty">중복 종목을 보려면 투자자를 2명 이상 고르세요.</p>
      ) : rows.length === 0 ? (
        <p className="empty">선택한 투자자들 사이에 겹치는 종목이 없습니다.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>종목</th><th className="num">투자자 수</th><th>보유 투자자 (포트폴리오 비중)</th><th className="num">합계 평가액</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.cusip}>
                  <td><Stock p={r} /></td>
                  <td className="num"><strong>{r.holders.length}</strong></td>
                  <td>
                    <div className="chips">
                      {r.holders.map((h) => (
                        <span key={h.investor.cik} className="chip">{h.investor.ko} {pct(h.position.weight)}</span>
                      ))}
                    </div>
                  </td>
                  <td className="num">{money(r.total)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
