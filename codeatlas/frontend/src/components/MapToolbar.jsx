const LEVELS = [
  { id: 'folder', label: '폴더' },
  { id: 'file', label: '파일' },
  { id: 'function', label: '함수' },
]

const EDGE_KINDS = [
  { id: 'calls', label: '호출', sample: 'solid' },
  { id: 'imports', label: 'import', sample: 'dashed' },
  { id: 'inherits', label: '상속', sample: 'dotted' },
]

export default function MapToolbar({ level, onLevel, edgeKinds, onEdgeKind, onFit, onRelayout, hasRoute, onClearRoute, stats }) {
  return (
    <div className="map-toolbar">
      <div className="segmented" role="group" aria-label="보기 단위">
        {LEVELS.map((l) => (
          <button key={l.id} type="button" className={level === l.id ? 'active' : ''} aria-pressed={level === l.id} onClick={() => onLevel(l.id)}>
            {l.label}
          </button>
        ))}
      </div>
      <div className="legend" aria-label="선 종류">
        {EDGE_KINDS.map((k) => (
          <label key={k.id} className="legend-item">
            <input type="checkbox" checked={edgeKinds.has(k.id)} onChange={() => onEdgeKind(k.id)} />
            <span className={`line ${k.sample}`} aria-hidden="true" />
            {k.label}
          </label>
        ))}
        <span className="legend-item route-key">
          <span className="line route" aria-hidden="true" />
          질문·투어 경로
        </span>
      </div>
      <div className="toolbar-actions">
        {hasRoute && (
          <button type="button" onClick={onClearRoute}>
            강조 지우기
          </button>
        )}
        <button type="button" onClick={onFit}>전체 보기</button>
        <button type="button" onClick={onRelayout}>다시 배치</button>
      </div>
      {stats && (
        <p className="map-stats">
          파일 {stats.files}개, 클래스 {stats.classes}개, 함수 {stats.functions}개, 찾아낸 호출 관계 {stats.calls_resolved + stats.calls_heuristic}개
        </p>
      )}
    </div>
  )
}
