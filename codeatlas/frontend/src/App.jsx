import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api.js'
import { countVisible, expandedForLevel, hasChildren, indexGraph, revealIds, visibleGraph } from './graphModel.js'
import TopBar from './components/TopBar.jsx'
import StartScreen from './components/StartScreen.jsx'
import MapToolbar from './components/MapToolbar.jsx'
import MapView from './components/MapView.jsx'
import DetailPanel from './components/DetailPanel.jsx'
import AskPanel from './components/AskPanel.jsx'
import TourPanel from './components/TourPanel.jsx'

const MAX_VISIBLE = 1500 // 한 화면에 그릴 최대 노드 수 (넘으면 느려진다)
const TOO_MANY = '한 번에 그리기엔 노드가 너무 많아요. 폴더나 파일을 두 번 눌러 필요한 부분만 펼쳐 보세요.'
const EMPTY_TOUR = { steps: null, current: 0, loading: false, error: '' }

function projectFromHash() {
  const m = /p=([^&]+)/.exec(window.location.hash)
  return m ? decodeURIComponent(m[1]) : null
}

export default function App() {
  const [health, setHealth] = useState(null)
  const [projects, setProjects] = useState([])
  const [source, setSource] = useState('')
  const [includeTests, setIncludeTests] = useState(false)
  const [job, setJob] = useState(null)
  const [notice, setNotice] = useState('')

  const [pid, setPid] = useState(null)
  const [data, setData] = useState(null)
  const [level, setLevel] = useState('file')
  const [expanded, setExpanded] = useState(() => new Set())
  const [edgeKinds, setEdgeKinds] = useState(() => new Set(['calls', 'inherits']))
  const [selectedId, setSelectedId] = useState(null)
  const [focus, setFocus] = useState(null)
  const [command, setCommand] = useState(null)
  const [route, setRoute] = useState(null) // { ids: [...], path: [...] } 강조할 노드와 경로
  const [tab, setTab] = useState('ask')
  const [qa, setQa] = useState(null)
  const [tour, setTour] = useState(EMPTY_TOUR)
  const pollRef = useRef(null)

  const index = useMemo(() => (data ? indexGraph(data) : null), [data])
  const view = useMemo(() => (index ? visibleGraph(index, expanded, edgeKinds) : null), [index, expanded, edgeKinds])

  // 강조할 노드·간선을 "지금 보이는" 노드 기준으로 바꾼다 (접혀 있으면 조상으로)
  const routeView = useMemo(() => {
    if (!route || !view) return null
    const nodes = new Set(route.ids.map((id) => view.rep(id)).filter(Boolean))
    const edges = new Set()
    for (let i = 0; i + 1 < route.path.length; i += 1) {
      const a = view.rep(route.path[i])
      const b = view.rep(route.path[i + 1])
      if (a && b && a !== b) edges.add(`${a}|${b}`)
    }
    return { nodes, edges }
  }, [route, view])

  const refreshProjects = useCallback(() => {
    api.projects().then(setProjects).catch(() => {})
  }, [])

  const openProject = useCallback(async (id) => {
    setNotice('')
    try {
      const g = await api.graph(id)
      const idx = indexGraph(g)
      const files = g.nodes.filter((n) => n.kind === 'file').length
      const lv = files <= 120 ? 'file' : 'folder'
      setData(g)
      setPid(id)
      setLevel(lv)
      setExpanded(expandedForLevel(idx, lv))
      setSelectedId(null)
      setRoute(null)
      setQa(null)
      setTour(EMPTY_TOUR)
      window.location.hash = `p=${encodeURIComponent(id)}`
    } catch (e) {
      setNotice(e.message)
    }
  }, [])

  useEffect(() => {
    api.health().then(setHealth).catch(() => setNotice('백엔드 서버에 연결할 수 없어요. backend 폴더에서 서버를 먼저 실행하세요.'))
    refreshProjects()
    const fromHash = projectFromHash()
    if (fromHash) openProject(fromHash)
    return () => clearTimeout(pollRef.current)
  }, [openProject, refreshProjects])

  async function analyze(value = source) {
    const src = value.trim()
    if (!src) return
    clearTimeout(pollRef.current)
    setSource(src)
    setNotice('')
    try {
      const res = await api.analyze(src, includeTests)
      const id = res.project_id
      setJob({ id, ...res.status })
      const tick = async () => {
        try {
          const st = await api.status(id)
          if (st.state === 'done') {
            setJob(null)
            refreshProjects()
            openProject(id)
            return
          }
          if (st.state === 'error') {
            setJob(null)
            setNotice(st.message)
            return
          }
          setJob({ id, ...st })
          pollRef.current = setTimeout(tick, 700)
        } catch (e) {
          setJob(null)
          setNotice(e.message)
        }
      }
      pollRef.current = setTimeout(tick, 300)
    } catch (e) {
      setNotice(e.message)
    }
  }

  function goHome() {
    setData(null)
    setPid(null)
    setRoute(null)
    setSelectedId(null)
    window.location.hash = ''
    refreshProjects()
  }

  function changeLevel(lv) {
    if (!index) return
    const next = expandedForLevel(index, lv)
    if (countVisible(index, next) > MAX_VISIBLE) {
      setNotice(TOO_MANY)
      return
    }
    setNotice('')
    setLevel(lv)
    setExpanded(next)
  }

  function toggle(id) {
    if (!index || !hasChildren(index, id)) return
    const next = new Set(expanded)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    if (countVisible(index, next) > MAX_VISIBLE) {
      setNotice(TOO_MANY)
      return
    }
    setExpanded(next)
  }

  function toggleEdgeKind(kind) {
    const next = new Set(edgeKinds)
    if (next.has(kind)) next.delete(kind)
    else next.add(kind)
    setEdgeKinds(next)
  }

  // 노드로 이동: 보이도록 조상을 펼치고, 선택하고, 화면 가운데로
  function navigate(id) {
    if (!index || !index.nodes.has(id)) return
    setExpanded((prev) => revealIds(index, prev, [id]))
    setSelectedId(id)
    setFocus({ id, at: Date.now() })
  }

  function showAnswer(res) {
    setQa(res)
    const ids = [...new Set([...res.path, ...res.nodes.map((n) => n.id)])]
    if (!index || ids.length === 0) {
      setRoute(null)
      return
    }
    setExpanded((prev) => revealIds(index, prev, ids))
    setRoute({ ids, path: res.path })
    setSelectedId(res.path[0] ?? ids[0])
    setCommand({ type: 'fitRoute', at: Date.now() })
  }

  function showTourStep(steps, i) {
    if (!steps || i < 0 || i >= steps.length) return
    const ids = steps.slice(0, i + 1).map((s) => s.id)
    setTour((t) => ({ ...t, steps, current: i }))
    setExpanded((prev) => revealIds(index, prev, ids))
    setRoute({ ids, path: ids })
    setSelectedId(steps[i].id)
    setCommand({ type: 'fitIds', ids: ids.slice(-2), at: Date.now() })
  }

  async function startTour() {
    setTour((t) => ({ ...t, loading: true, error: '' }))
    try {
      const res = await api.tour(pid)
      setTour({ steps: res.steps, current: 0, loading: false, error: '' })
      showTourStep(res.steps, 0)
    } catch (e) {
      setTour((t) => ({ ...t, loading: false, error: e.message }))
    }
  }

  const qualnameOf = (id) => index?.nodes.get(id)?.qualname ?? id
  const llmOn = Boolean(health?.llm?.available)

  return (
    <div className="app">
      <TopBar
        source={source}
        onSource={setSource}
        includeTests={includeTests}
        onIncludeTests={setIncludeTests}
        onSubmit={() => analyze()}
        busy={Boolean(job)}
        health={health}
        projectName={data?.project?.name}
        onHome={goHome}
      />
      {job && (
        <div className="progress" role="status">
          <div className="bar" style={{ width: `${Math.round((job.progress ?? 0) * 100)}%` }} />
          <span>{job.message}</span>
        </div>
      )}
      {notice && (
        <div className="notice" role="alert">
          <span>{notice}</span>
          <button type="button" onClick={() => setNotice('')} aria-label="알림 닫기">
            ×
          </button>
        </div>
      )}
      <main className={`workspace ${data ? 'has-map' : ''}`}>
        {data ? (
          <>
            <section className="side">
              <div className="tabs" role="tablist">
                <button type="button" role="tab" aria-selected={tab === 'ask'} className={tab === 'ask' ? 'active' : ''} onClick={() => setTab('ask')}>
                  질문하기
                </button>
                <button type="button" role="tab" aria-selected={tab === 'tour'} className={tab === 'tour' ? 'active' : ''} onClick={() => setTab('tour')}>
                  읽기 투어
                </button>
              </div>
              <div className="side-body" hidden={tab !== 'ask'}>
                <AskPanel pid={pid} llmOn={llmOn} result={qa} onResult={showAnswer} onNavigate={navigate} qualnameOf={qualnameOf} />
              </div>
              <div className="side-body" hidden={tab !== 'tour'}>
                <TourPanel
                  steps={tour.steps}
                  current={tour.current}
                  loading={tour.loading}
                  error={tour.error}
                  onStart={startTour}
                  onGo={(i) => showTourStep(tour.steps, i)}
                />
              </div>
            </section>
            <section className="map">
              <MapToolbar
                level={level}
                onLevel={changeLevel}
                edgeKinds={edgeKinds}
                onEdgeKind={toggleEdgeKind}
                onFit={() => setCommand({ type: 'fit', at: Date.now() })}
                onRelayout={() => setCommand({ type: 'relayout', at: Date.now() })}
                hasRoute={Boolean(route)}
                onClearRoute={() => setRoute(null)}
                stats={data.project?.stats}
              />
              <MapView
                view={view}
                selectedId={selectedId && view ? view.rep(selectedId) : null}
                route={routeView}
                focus={focus}
                command={command}
                onSelect={setSelectedId}
                onToggle={toggle}
              />
            </section>
            <DetailPanel pid={pid} nodeId={selectedId} expanded={selectedId ? expanded.has(selectedId) : false} onNavigate={navigate} onToggle={toggle} />
          </>
        ) : (
          <section className="map map-empty">
            <StartScreen
              projects={projects}
              onSample={(url) => {
                setSource(url)
                analyze(url)
              }}
              onOpen={openProject}
            />
          </section>
        )}
      </main>
    </div>
  )
}
