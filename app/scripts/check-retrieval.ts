// 벡터스토어 스팟체크: 고정 질문을 넣고 상위 근거와 약한 근거 여부를 확인합니다.
//   npm run check
// 질문 임베딩은 브라우저와 같은 src/embedCore.ts, 검색은 같은 src/retrieval.ts를 씁니다.
import { Tokenizer } from '@huggingface/tokenizers'
import * as ort from 'onnxruntime-web'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createEmbedder } from '../src/embedCore.ts'
import { search, SETTINGS, type Doc } from '../src/retrieval.ts'

const APP = join(dirname(fileURLToPath(import.meta.url)), '..')
const cache = (n: string) => readFileSync(join(APP, '.model-cache', n))

// [질문, 상위 3개 안에 기대하는 section 조각 (없으면 도메인 밖 질문)]
const QUESTIONS: [string, string | null][] = [
  ['워런 버핏의 최대 보유 종목은?', '워런 버핏 · 2026년 2분기 · 포트폴리오 개요'],
  ['빌 애크먼이 이번 분기에 새로 산 종목은?', '빌 애크먼 · 2026년 2분기 · 신규 매수'],
  ['드러켄밀러가 전량 매도한 종목은?', '스탠리 드러켄밀러 · 2026년 2분기 · 주식 수 변화와 전량 매도'],
  ['여러 투자자가 함께 새로 매수한 종목은?', '중복 신규 매수'],
  ['아마존을 보유한 투자자는 누구야?', '중복 보유'],
  ['13F는 언제까지 제출해야 해?', '13F 제도 · 제출 기한과 지연'],
  ['13F에 공매도도 나와?', '13F 제도 · 신고하지 않는 것'],
  ['애플 지금 사도 돼?', null],
  ['엔비디아 현재 주가는?', null],
  ['내일 날씨 어때?', null],
]

ort.env.wasm.numThreads = 1
const tokenizer = new Tokenizer(JSON.parse(cache('tokenizer.json').toString()), JSON.parse(cache('tokenizer_config.json').toString()))
const embed = await createEmbedder(ort, tokenizer, cache('onnx__model_no_gather_q4.onnx'), cache('onnx__model_no_gather_q4.onnx_data'))
const docs: Doc[] = JSON.parse(readFileSync(join(APP, 'public', 'data', 'docs.json'), 'utf-8')).docs

let pass = 0
for (const [q, expect] of QUESTIONS) {
  const [v] = await embed([q])
  const r = search(docs, v, q)
  const top3 = r.hits.slice(0, 3)
  const hit1 = expect ? r.hits[0].chunk.section.includes(expect) : null
  const hit3 = expect ? top3.some((h) => h.chunk.section.includes(expect)) : null
  const bm = r.hits.findIndex((h) => h.method === 'bm25' && expect && h.chunk.section.includes(expect))
  if (expect ? hit3 : r.weak) pass++
  console.log(`\nQ: ${q}`)
  console.log(`   최고 유사도 ${r.topScore.toFixed(3)}${r.weak ? ` (약한 근거 < ${SETTINGS.weakThreshold})` : ''}` +
    (expect ? ` · top-1 ${hit1 ? 'O' : 'X'} · top-3 ${hit3 ? 'O' : 'X'}${bm >= 0 ? ' · BM25로 보충' : ''}` : ' · 도메인 밖 질문'))
  for (const h of top3) console.log(`   ${h.chunk.id} ${h.method} ${h.score.toFixed(3)}  ${h.chunk.section}`)
}
console.log(`\n통과 ${pass} / ${QUESTIONS.length} (근거 있는 질문: 기대 조각이 top-3, 도메인 밖 질문: 약한 근거 표시)`)
