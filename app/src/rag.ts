// 브라우저 RAG 파이프라인: 모델 준비(Cache Storage)와 벡터스토어 로드. 검색·프롬프트는 retrieval.ts
import { Tokenizer } from '@huggingface/tokenizers'
import * as ort from 'onnxruntime-web'
import { createEmbedder, DIM, FILES, HF_BASE, type Embed } from './embedCore'
import type { Doc } from './retrieval'

export * from './retrieval'


const ORT_VERSION = '1.30.0'
ort.env.wasm.wasmPaths = `https://cdn.jsdelivr.net/npm/onnxruntime-web@${ORT_VERSION}/dist/`
ort.env.wasm.numThreads = 1 // GitHub Pages는 SharedArrayBuffer를 쓸 수 없어 단일 스레드로 고정

// ---------------------------------------------------------------- 모델 준비 (Cache Storage)

export interface LoadProgress { file: string; loaded: number; total: number; cached: boolean }
const CACHE_NAME = 'embeddinggemma-no-gather-q4'

/** Hugging Face resolve URL은 서명된 CDN URL로 리다이렉트되어 HTTP 캐시 적중이 불안정하므로,
 *  원래 URL을 키로 Cache Storage에 직접 보관합니다. 저장이 실패해도 다운로드는 계속합니다. */
async function cachedFetch(url: string, onProgress: (p: LoadProgress) => void): Promise<Uint8Array> {
  const file = url.split('/').pop()!
  let cache: Cache | null = null
  try {
    cache = await caches.open(CACHE_NAME)
    const hit = await cache.match(url)
    if (hit) {
      const buf = new Uint8Array(await hit.arrayBuffer())
      onProgress({ file, loaded: buf.length, total: buf.length, cached: true })
      return buf
    }
  } catch {
    cache = null
  }

  const res = await fetch(url)
  if (!res.ok || !res.body) throw new Error(`${file}: HTTP ${res.status}`)
  const total = Number(res.headers.get('content-length')) || 0
  const parts: Uint8Array[] = []
  let loaded = 0
  const reader = res.body.getReader()
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    parts.push(value)
    loaded += value.length
    onProgress({ file, loaded, total, cached: false })
  }
  const buf = new Uint8Array(loaded)
  let off = 0
  for (const p of parts) { buf.set(p, off); off += p.length }

  for (let attempt = 0; cache && attempt < 2; attempt++) {
    try {
      await cache.put(url, new Response(buf))
      break
    } catch { /* 한 번 더 시도하고, 그래도 실패하면 캐시 없이 진행 */ }
  }
  return buf
}

let embedder: Promise<Embed> | null = null
// 로딩은 한 번만 하지만 여러 화면(또는 개발 모드의 이중 호출)이 진행 상황을 받을 수 있게 합니다
const progress = new Map<string, LoadProgress>()
const listeners = new Set<(files: LoadProgress[]) => void>()

export function loadEmbedder(onProgress?: (files: LoadProgress[]) => void): Promise<Embed> {
  if (onProgress) {
    listeners.add(onProgress)
    if (progress.size) onProgress([...progress.values()])
  }
  embedder ??= (async () => {
    progress.clear()
    const report = (p: LoadProgress) => {
      progress.set(p.file, p)
      for (const l of listeners) l([...progress.values()])
    }
    const get = (name: string) => cachedFetch(HF_BASE + name, report)
    const json = async (name: string) => JSON.parse(new TextDecoder().decode(await get(name)))
    const tokenizer = new Tokenizer(await json(FILES.tokenizer), await json(FILES.tokenizerConfig))
    const [model, data] = [await get(FILES.model), await get(FILES.modelData)]
    return createEmbedder(ort, tokenizer, model, data)
  })().catch((e) => {
    embedder = null // 실패하면 다음 시도에서 다시 받습니다
    throw e
  })
  return embedder
}

let docsPromise: Promise<Doc[]> | null = null
export function loadDocs(): Promise<Doc[]> {
  docsPromise ??= fetch('./data/docs.json')
    .then((r) => { if (!r.ok) throw new Error(`docs.json: HTTP ${r.status}`); return r.json() })
    .then((d) => {
      if (d.dim !== DIM) throw new Error(`벡터 차원이 ${d.dim}입니다 (기대 ${DIM})`)
      return d.docs as Doc[]
    })
  return docsPromise
}
