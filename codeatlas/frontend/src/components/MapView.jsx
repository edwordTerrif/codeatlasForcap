import { useEffect, useRef, useState } from 'react'
import cytoscape from 'cytoscape'
import fcose from 'cytoscape-fcose'

cytoscape.use(fcose) // 묶음(compound) 노드 배치에 강한 레이아웃

// 해도(nautical chart)처럼: 묶음(커뮤니티)마다 차분한 색, 질문 경로·투어 경로는 항해용 자홍색
export const COMMUNITY_COLORS = ['#7FB08A', '#D8B060', '#7C9CC9', '#B08CC6', '#5FA9A2', '#C9876E', '#A7A85A', '#C77C97']
const NO_COMMUNITY = '#B9C4CA'
const ROUTE = '#C2187A'
const INK = '#1E2A33'
const MONO = 'ui-monospace, SFMono-Regular, D2Coding, Menlo, Consolas, monospace'
const reduceMotion = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

const BASE_SIZE = { folder: 44, file: 34, class: 30, function: 22, method: 20 }

function nodeLabel(n) {
  if (n.kind === 'folder') return `${n.name}/`
  if (n.kind === 'function' || n.kind === 'method') return `${n.name}()`
  return n.name
}

function nodeColor(n) {
  if (n.kind === 'folder') return '#D6E8EE'
  return n.community >= 0 ? COMMUNITY_COLORS[n.community % COMMUNITY_COLORS.length] : NO_COMMUNITY
}

function nodeSize(n) {
  return Math.round((BASE_SIZE[n.kind] ?? 24) * (0.7 + 0.9 * (n.importance ?? 0)))
}

const STYLE = [
  {
    selector: 'node',
    style: {
      label: 'data(label)',
      'font-family': MONO,
      'font-size': 12,
      color: INK,
      'text-valign': 'bottom',
      'text-margin-y': 4,
      'text-wrap': 'ellipsis',
      'text-max-width': 180,
      'text-background-color': '#EAF2F4',
      'text-background-opacity': 0.7,
      'text-background-padding': 1,
      'min-zoomed-font-size': 7,
      width: 'data(size)',
      height: 'data(size)',
      'background-color': 'data(color)',
      'border-width': 1,
      'border-color': 'rgba(30,42,51,0.35)',
    },
  },
  { selector: 'node[kind = "folder"]', style: { shape: 'round-rectangle', 'border-style': 'dashed', 'border-color': '#7FA9B8', 'border-width': 1.5, 'font-size': 15 } },
  { selector: 'node[kind = "file"]', style: { shape: 'round-rectangle', 'font-size': 14 } },
  { selector: 'node[kind = "class"]', style: { shape: 'round-hexagon', 'font-size': 13 } },
  { selector: 'node[kind = "method"]', style: { 'border-style': 'dotted', 'border-width': 1.5 } },
  {
    selector: 'node.compound',
    style: {
      shape: 'round-rectangle',
      'background-color': '#F7FBFC',
      'background-opacity': 0.8,
      'border-color': '#9DBFCB',
      'border-style': 'dashed',
      'border-width': 1.2,
      'text-valign': 'top',
      'text-halign': 'center',
      'text-margin-y': -4,
      'font-size': 12,
      'font-weight': 600,
      padding: 14,
    },
  },
  { selector: 'node.compound[kind = "file"]', style: { 'background-color': 'data(color)', 'background-opacity': 0.14, 'border-style': 'solid', 'border-color': 'data(color)' } },
  { selector: 'node.compound[kind = "class"]', style: { 'background-opacity': 0.08, 'border-style': 'solid' } },
  {
    selector: 'edge',
    style: {
      width: 'data(width)',
      'curve-style': 'bezier',
      'line-color': '#6F8794',
      'target-arrow-color': '#6F8794',
      'target-arrow-shape': 'triangle',
      'arrow-scale': 0.8,
      opacity: 0.55,
    },
  },
  { selector: 'edge[kind = "imports"]', style: { 'line-style': 'dashed', 'line-color': '#9FB3BE', 'target-arrow-color': '#9FB3BE' } },
  { selector: 'edge[kind = "inherits"]', style: { 'line-style': 'dotted', 'target-arrow-fill': 'hollow', 'line-color': '#3F5E6E', 'target-arrow-color': '#3F5E6E' } },
  { selector: 'edge.heuristic', style: { 'line-style': 'dotted', opacity: 0.35 } },
  { selector: '.faded', style: { opacity: 0.14 } },
  {
    selector: 'node.route',
    style: {
      'border-color': ROUTE,
      'border-width': 4,
      'border-style': 'solid',
      opacity: 1,
      color: ROUTE,
      'font-weight': 700,
      'min-zoomed-font-size': 0, // 경로 위 노드 이름은 멀리서도 보이게
      'text-background-opacity': 0.92,
    },
  },
  { selector: 'edge.route', style: { 'line-color': ROUTE, 'target-arrow-color': ROUTE, 'line-style': 'solid', width: 4, opacity: 1 } },
  { selector: 'node.selected', style: { 'border-color': INK, 'border-width': 3, 'border-style': 'solid', 'overlay-color': ROUTE, 'overlay-opacity': 0.08, 'min-zoomed-font-size': 0 } },
]

// 같은 묶음(파일·클래스) 안의 간선은 당기고, 묶음을 넘나드는 간선은 느슨하게 → 묶음끼리 겹치지 않는다
const sameParent = (e) => e.source().parent().same(e.target().parent())

const LAYOUT = {
  name: 'fcose',
  quality: 'default',
  animate: false,
  fit: true,
  padding: 40,
  nodeDimensionsIncludeLabels: true,
  packComponents: true,
  nodeRepulsion: () => 6500,
  idealEdgeLength: (e) => (sameParent(e) ? 60 : 230),
  edgeElasticity: (e) => (sameParent(e) ? 0.45 : 0.03),
  nestingFactor: 0.1,
  gravity: 0.25,
  gravityCompound: 1.0,
  gravityRangeCompound: 1.5,
  numIter: 2500,
  tile: true, // 연결이 없는 노드는 바둑판처럼 정리
  tilingPaddingVertical: 12,
  tilingPaddingHorizontal: 12,
}

export default function MapView({ view, selectedId, route, focus, command, onSelect, onToggle }) {
  const containerRef = useRef(null)
  const cyRef = useRef(null)
  const layoutDone = useRef(Promise.resolve())
  const handlers = useRef({ onSelect, onToggle })
  handlers.current = { onSelect, onToggle }
  const viewRef = useRef(view)
  viewRef.current = view
  const [tip, setTip] = useState(null)
  const [busy, setBusy] = useState(false)

  // 처음 한 번 Cytoscape 생성
  useEffect(() => {
    const cy = cytoscape({
      container: containerRef.current,
      style: STYLE,
      minZoom: 0.02,
      maxZoom: 3,
      boxSelectionEnabled: false,
    })
    cy.on('tap', 'node', (e) => handlers.current.onSelect(e.target.id()))
    cy.on('dbltap', 'node', (e) => handlers.current.onToggle(e.target.id()))
    cy.on('tap', (e) => {
      if (e.target === cy) handlers.current.onSelect(null)
    })
    cy.on('mouseover', 'node', (e) => {
      const n = e.target
      if (n.isParent()) return
      const p = n.renderedPosition()
      setTip({ x: p.x, y: p.y - n.renderedOuterHeight() / 2 - 8, title: n.data('qualname'), text: n.data('summary') })
    })
    cy.on('mouseout', 'node', () => setTip(null))
    cy.on('viewport', () => setTip(null))
    cyRef.current = cy
    window.__codeatlasCy = cy // 브라우저 개발자 도구에서 그래프를 직접 살펴볼 때 쓰세요
    return () => {
      cy.destroy()
      cyRef.current = null
    }
  }, [])

  // 보이는 노드·간선이 바뀌면 다시 그리고 배치
  useEffect(() => {
    const cy = cyRef.current
    if (!cy || !view) return
    const prev = new Map()
    cy.nodes().forEach((n) => prev.set(n.id(), { ...n.position() }))
    const els = []
    for (const n of view.nodes) {
      els.push({
        group: 'nodes',
        data: {
          id: n.id,
          parent: n.vparent ?? undefined,
          label: nodeLabel(n),
          kind: n.kind,
          qualname: n.qualname ?? n.name,
          summary: n.summary ?? '',
          color: nodeColor(n),
          size: nodeSize(n),
        },
        classes: n.compound ? 'compound' : '',
      })
    }
    for (const e of view.edges) {
      els.push({
        group: 'edges',
        data: { id: e.id, source: e.source, target: e.target, kind: e.kind, width: Math.min(6, 1 + Math.log2(1 + e.weight)) },
        classes: e.heuristic ? 'heuristic' : '',
      })
    }
    cy.batch(() => {
      cy.elements().remove()
      cy.add(els)
    })
    // 새로 나타난 노드는 원래 있던 조상 근처에서 출발 → 펼치고 접어도 지도가 크게 흔들리지 않게
    const leaves = cy.nodes().filter((n) => n.isChildless())
    let kept = 0
    leaves.forEach((n) => {
      const p = prev.get(n.id())
      if (p) {
        n.position(p)
        kept += 1
        return
      }
      let a = n.parent()
      while (a.nonempty() && !prev.has(a.id())) a = a.parent()
      const base = a.nonempty() ? prev.get(a.id()) : null
      if (base) n.position({ x: base.x + (Math.random() - 0.5) * 60, y: base.y + (Math.random() - 0.5) * 60 })
    })
    // 대부분 그대로 있는 노드면 이어서 배치(지도가 덜 흔들림), 새 노드가 많으면 처음부터 배치
    const layout = cy.layout({ ...LAYOUT, randomize: leaves.length === 0 || kept / leaves.length < 0.6 })
    layoutDone.current = layout.promiseOn('layoutstop')
    if (view.nodes.length > 400) {
      // 노드가 많으면 배치에 몇 초 걸린다 → 안내 문구를 먼저 그린 뒤 배치
      setBusy(true)
      const timer = setTimeout(() => {
        layout.run()
        setBusy(false)
      }, 30)
      return () => {
        clearTimeout(timer)
        setBusy(false)
      }
    }
    setBusy(false)
    layout.run()
    return undefined
  }, [view])

  // 선택·경로 강조
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.batch(() => {
      cy.elements().removeClass('selected route faded')
      if (route && route.nodes.size > 0) {
        const keep = cy.collection()
        route.nodes.forEach((id) => {
          const n = cy.getElementById(id)
          if (n.nonempty()) {
            n.addClass('route')
            keep.merge(n).merge(n.ancestors())
          }
        })
        cy.edges().forEach((e) => {
          if (route.edges.has(`${e.data('source')}|${e.data('target')}`)) e.addClass('route')
        })
        cy.elements().not(keep).not('.route').addClass('faded')
      }
      if (selectedId) cy.getElementById(selectedId).addClass('selected')
    })
  }, [route, selectedId, view])

  // 특정 노드로 이동 (focus가 바뀔 때만. 같은 렌더에서 view도 바뀌면 배치가 끝난 뒤 이동)
  useEffect(() => {
    if (!focus) return
    layoutDone.current.then(() => {
      const cy = cyRef.current
      const id = viewRef.current?.rep(focus.id) ?? focus.id
      const n = cy?.getElementById(id)
      if (!n || n.empty()) return
      cy.animate({ center: { eles: n }, zoom: Math.max(cy.zoom(), 0.9) }, { duration: reduceMotion ? 0 : 350 })
    })
  }, [focus])

  // 툴바 명령: 전체 보기 / 경로에 맞추기 / 다시 배치
  useEffect(() => {
    if (!command) return
    layoutDone.current.then(() => {
      const cy = cyRef.current
      if (!cy) return
      const duration = reduceMotion ? 0 : 350
      if (command.type === 'fit') cy.animate({ fit: { padding: 40 } }, { duration })
      if (command.type === 'fitRoute') {
        const els = cy.$('.route')
        if (els.nonempty()) {
          const clamp = () => {
            if (cy.zoom() <= 1.3) return // 노드가 한두 개면 너무 크게 확대되지 않도록
            const bb = els.boundingBox()
            cy.zoom({ level: 1.3, position: { x: (bb.x1 + bb.x2) / 2, y: (bb.y1 + bb.y2) / 2 } })
          }
          cy.animate({ fit: { eles: els, padding: 90 } }, { duration, complete: clamp })
        }
      }
      if (command.type === 'fitIds') {
        // 투어: 이전 단계와 현재 단계가 함께 보이도록
        const rep = viewRef.current?.rep ?? ((x) => x)
        let els = cy.collection()
        for (const id of command.ids) els = els.union(cy.getElementById(rep(id)))
        if (els.nonempty()) {
          const clamp = () => {
            if (cy.zoom() <= 1.2) return
            const bb = els.boundingBox()
            cy.zoom({ level: 1.2, position: { x: (bb.x1 + bb.x2) / 2, y: (bb.y1 + bb.y2) / 2 } })
          }
          cy.animate({ fit: { eles: els, padding: 140 } }, { duration, complete: clamp })
        }
      }
      if (command.type === 'relayout') {
        const layout = cy.layout({ ...LAYOUT, randomize: true })
        layoutDone.current = layout.promiseOn('layoutstop')
        layout.run()
      }
    })
  }, [command])

  return (
    <div className="map-canvas">
      <div ref={containerRef} className="map-cy" aria-label="코드 지도" />
      {busy && <div className="map-busy">노드가 많아서 지도를 배치하는 중이에요…</div>}
      {tip && (
        <div className="map-tip" style={{ left: tip.x, top: tip.y }}>
          <code>{tip.title}</code>
          {tip.text && <p>{tip.text}</p>}
        </div>
      )}
    </div>
  )
}
