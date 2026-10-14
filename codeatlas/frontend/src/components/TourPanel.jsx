export default function TourPanel({ steps, current, loading, error, onStart, onGo }) {
  if (!steps) {
    return (
      <div className="tour">
        <p>
          실행이 시작되는 지점부터 꼭 읽어야 할 핵심 코드를 순서대로 따라가요. 처음 보는 프로젝트라면 여기서부터 시작해 보세요.
        </p>
        <button type="button" className="primary" onClick={onStart} disabled={loading}>
          {loading ? '경로를 짜는 중…' : '투어 시작'}
        </button>
        {error && <p className="error">{error}</p>}
      </div>
    )
  }
  const step = steps[current]
  return (
    <div className="tour">
      <div className="tour-nav">
        <button type="button" onClick={() => onGo(current - 1)} disabled={current === 0}>
          이전
        </button>
        <span>
          {current + 1} / {steps.length}
        </span>
        <button type="button" className="primary" onClick={() => onGo(current + 1)} disabled={current === steps.length - 1}>
          다음
        </button>
      </div>
      {step && (
        <article className="tour-current">
          <h3>
            <code>{step.title}</code>
          </h3>
          <p className="where">
            {step.path}:{step.lineno}
          </p>
          <p className="reason">{step.reason}</p>
          {step.summary && <p>{step.summary}</p>}
        </article>
      )}
      <ol className="tour-steps">
        {steps.map((s, i) => (
          <li key={s.id} className={i === current ? 'current' : ''}>
            <button type="button" onClick={() => onGo(i)} aria-current={i === current ? 'step' : undefined}>
              <code>{s.title}</code>
            </button>
          </li>
        ))}
      </ol>
      <button type="button" className="link" onClick={onStart}>
        투어 다시 만들기
      </button>
    </div>
  )
}
