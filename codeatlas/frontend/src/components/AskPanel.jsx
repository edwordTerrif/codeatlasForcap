import { useState } from 'react'
import { api } from '../api.js'

const EXAMPLES = ['요청을 보내면 어떤 순서로 처리돼?', '설정은 어디서 읽어?', '에러는 어떻게 처리해?']

// 답변 속 [1] 같은 근거 번호는 눌러서 해당 코드로 이동할 수 있게, `코드`와 **강조**는 간단히 표시
function RichText({ text, onRef }) {
  const parts = text.split(/(\[\d+\]|`[^`]+`|\*\*[^*]+\*\*)/g)
  return parts.map((p, i) => {
    const ref = /^\[(\d+)\]$/.exec(p)
    if (ref) {
      return (
        <button key={i} type="button" className="ref" onClick={() => onRef(Number(ref[1]))}>
          {p}
        </button>
      )
    }
    if (p.startsWith('`') && p.endsWith('`') && p.length > 2) return <code key={i}>{p.slice(1, -1)}</code>
    if (p.startsWith('**') && p.endsWith('**') && p.length > 4) return <strong key={i}>{p.slice(2, -2)}</strong>
    return <span key={i}>{p}</span>
  })
}

export default function AskPanel({ pid, llmOn, result, onResult, onNavigate, qualnameOf }) {
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function submit(q) {
    const text = (q ?? question).trim()
    if (!text || !pid) return
    setQuestion(text)
    setLoading(true)
    setError('')
    try {
      onResult(await api.ask(pid, text))
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const byRef = (ref) => result?.nodes.find((n) => n.ref === ref)

  return (
    <div className="ask">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        <label htmlFor="question">이 코드에 대해 궁금한 것</label>
        <textarea
          id="question"
          rows={3}
          value={question}
          placeholder="예: 로그인은 어디서 처리돼?"
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submit()
          }}
        />
        <button type="submit" className="primary" disabled={loading || !question.trim()}>
          {loading ? '코드를 찾는 중…' : '질문하기'}
        </button>
      </form>
      {!llmOn && (
        <p className="note">AI가 꺼져 있어서 관련 코드와 호출 흐름만 찾아 드려요. backend/.env에 LLM을 설정하면 답변 문장도 만들어요.</p>
      )}
      {!result && !loading && (
        <div className="examples">
          {EXAMPLES.map((ex) => (
            <button key={ex} type="button" className="chip" onClick={() => submit(ex)}>
              {ex}
            </button>
          ))}
        </div>
      )}
      {error && <p className="error">{error}</p>}
      {result && (
        <div className="answer">
          <div className="answer-text">
            <RichText text={result.answer} onRef={(ref) => byRef(ref) && onNavigate(byRef(ref).id)} />
          </div>
          {result.path.length > 1 && (
            <div className="flow" aria-label="호출 흐름">
              {result.path.map((id, i) => (
                <span key={id} className="flow-step">
                  {i > 0 && <span className="flow-arrow" aria-hidden="true">→</span>}
                  <button type="button" onClick={() => onNavigate(id)}>
                    <code>{qualnameOf(id)}</code>
                  </button>
                </span>
              ))}
            </div>
          )}
          <h3>근거로 쓴 코드</h3>
          <ol className="evidence">
            {result.nodes.map((n) => (
              <li key={n.id}>
                <button type="button" onClick={() => onNavigate(n.id)}>
                  <code>{n.qualname}</code>
                  <span className="where">
                    {n.path}:{n.lineno}
                  </span>
                  {n.summary && <span className="sum">{n.summary}</span>}
                </button>
              </li>
            ))}
          </ol>
          {result.keywords?.length > 0 && <p className="muted">검색에 쓴 키워드: {result.keywords.join(', ')}</p>}
        </div>
      )}
    </div>
  )
}
