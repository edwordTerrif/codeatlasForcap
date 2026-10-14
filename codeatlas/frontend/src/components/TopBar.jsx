export default function TopBar({ source, onSource, includeTests, onIncludeTests, onSubmit, busy, health, projectName, onHome }) {
  const llm = health?.llm
  return (
    <header className="topbar">
      <button type="button" className="brand" onClick={onHome} title="처음 화면으로">
        <svg viewBox="0 0 32 32" width="26" height="26" aria-hidden="true">
          <circle cx="16" cy="16" r="14" fill="#EAF2F4" stroke="#1E2A33" strokeWidth="2" />
          <path d="M16 5 L19 16 L16 27 L13 16 Z" fill="#C2187A" />
          <circle cx="16" cy="16" r="2" fill="#1E2A33" />
        </svg>
        <span>CodeAtlas</span>
      </button>
      {projectName && <span className="project-name" title="지금 보고 있는 저장소">{projectName}</span>}
      <form
        className="analyze-form"
        onSubmit={(e) => {
          e.preventDefault()
          onSubmit()
        }}
      >
        <label className="sr-only" htmlFor="source">GitHub 저장소 주소 또는 폴더 경로</label>
        <input
          id="source"
          value={source}
          onChange={(e) => onSource(e.target.value)}
          placeholder="https://github.com/사용자/저장소 또는 내 컴퓨터의 폴더 경로"
          spellCheck={false}
          autoComplete="off"
        />
        <label className="check">
          <input type="checkbox" checked={includeTests} onChange={(e) => onIncludeTests(e.target.checked)} />
          테스트·문서 포함
        </label>
        <button type="submit" className="primary" disabled={busy || !source.trim()}>
          {busy ? '그리는 중…' : '지도 그리기'}
        </button>
      </form>
      <span className={`ai-badge ${llm?.available ? 'on' : 'off'}`} title={llm?.warning || (llm?.available ? `${llm.provider} / ${llm.model}` : 'backend/.env에서 LLM_PROVIDER를 설정하면 AI 요약과 답변을 쓸 수 있어요')}>
        {llm?.available ? `AI: ${llm.model}` : 'AI 꺼짐'}
      </span>
    </header>
  )
}
