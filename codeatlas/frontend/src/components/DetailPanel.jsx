import { useEffect, useState } from 'react'
import { api } from '../api.js'

export const KIND_KO = { folder: '폴더', file: '파일', class: '클래스', function: '함수', method: '메서드' }
const SOURCE_KO = { llm: 'AI 요약', docstring: '코드 주석(docstring)', auto: '구조로 만든 자동 요약' }

function RelList({ title, items, onNavigate }) {
  const [all, setAll] = useState(false)
  if (!items || items.length === 0) return null
  const shown = all ? items : items.slice(0, 6)
  return (
    <section className="rel">
      <h3>
        {title} <span className="count">{items.length}</span>
      </h3>
      <ul>
        {shown.map((it) => (
          <li key={it.id}>
            <button type="button" onClick={() => onNavigate(it.id)} title={it.summary || it.qualname}>
              <code>{it.qualname}</code>
              <span className="where">{it.path}</span>
            </button>
          </li>
        ))}
      </ul>
      {items.length > 6 && (
        <button type="button" className="link" onClick={() => setAll(!all)}>
          {all ? '접기' : `${items.length - 6}개 더 보기`}
        </button>
      )}
    </section>
  )
}

function CodeBlock({ code, start, truncated }) {
  if (!code) return null
  const lines = code.split('\n')
  return (
    <section className="code">
      <h3>코드</h3>
      <pre>
        {lines.map((line, i) => (
          <div key={i} className="code-line">
            <span className="ln">{start + i}</span>
            <span>{line || ' '}</span>
          </div>
        ))}
      </pre>
      {truncated && <p className="muted">길어서 앞부분만 보여 줘요.</p>}
    </section>
  )
}

export default function DetailPanel({ pid, nodeId, expanded, onNavigate, onToggle }) {
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!pid || !nodeId) {
      setDetail(null)
      return undefined
    }
    let alive = true
    setError('')
    api
      .node(pid, nodeId)
      .then((d) => alive && setDetail(d))
      .catch((e) => alive && setError(e.message))
    return () => {
      alive = false
    }
  }, [pid, nodeId])

  if (!nodeId) {
    return (
      <aside className="detail empty">
        <p>지도에서 노드를 누르면 코드와 설명이 여기에 나와요.</p>
        <p>두 번 누르면 폴더·파일·클래스의 안쪽이 펼쳐져요.</p>
      </aside>
    )
  }
  if (error) return <aside className="detail"><p className="error">{error}</p></aside>
  if (!detail || detail.node.id !== nodeId) return <aside className="detail"><p className="muted">불러오는 중…</p></aside>

  const n = detail.node
  const canExpand = detail.children.length > 0
  return (
    <aside className="detail">
      <header>
        <span className={`kind kind-${n.kind}`}>{KIND_KO[n.kind]}</span>
        <h2>
          <code>{n.kind === 'folder' ? `${n.name}/` : n.name}</code>
        </h2>
        {n.path && (
          <p className="where">
            {n.path}
            {n.kind !== 'folder' && n.kind !== 'file' ? `:${n.lineno}` : ''}
          </p>
        )}
      </header>
      {n.summary && (
        <section className="summary">
          <p>{n.summary}</p>
          <p className="muted">출처: {SOURCE_KO[n.summary_source] ?? '요약'}</p>
        </section>
      )}
      {n.error && <p className="error">{n.error}</p>}
      {n.signature && <pre className="signature">{n.signature}</pre>}
      {canExpand && (
        <button type="button" onClick={() => onToggle(n.id)}>
          {expanded ? '안쪽 접기' : `안쪽 펼치기 (${detail.children.length}개)`}
        </button>
      )}
      <RelList title="이 코드가 부르는 것" items={detail.callees} onNavigate={onNavigate} />
      <RelList title="이 코드를 부르는 곳" items={detail.callers} onNavigate={onNavigate} />
      <RelList title="부모 클래스" items={detail.bases} onNavigate={onNavigate} />
      <RelList title="자식 클래스" items={detail.subclasses} onNavigate={onNavigate} />
      <RelList title="가져다 쓰는 파일 (import)" items={detail.imports} onNavigate={onNavigate} />
      <RelList title="이 파일을 가져다 쓰는 곳" items={detail.imported_by} onNavigate={onNavigate} />
      {n.kind === 'folder' && <RelList title="안에 있는 항목" items={detail.children} onNavigate={onNavigate} />}
      <CodeBlock code={detail.code} start={detail.code_start} truncated={detail.code_truncated} />
    </aside>
  )
}
