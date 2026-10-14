// 전체 코드 그래프에서 "지금 화면에 보일 만큼"만 골라내는 순수 함수들.
// 접힌 노드 안쪽의 관계는 가장 가까운 보이는 조상 노드로 모아서(합쳐서) 보여 준다.

const KIND_ORDER = { folder: 0, file: 1, class: 2, function: 3, method: 4 }

export function indexGraph(data) {
  const nodes = new Map(data.nodes.map((n) => [n.id, n]))
  const children = new Map()
  for (const n of data.nodes) {
    if (n.parent == null) continue
    if (!children.has(n.parent)) children.set(n.parent, [])
    children.get(n.parent).push(n.id)
  }
  for (const list of children.values()) {
    list.sort((a, b) => {
      const na = nodes.get(a)
      const nb = nodes.get(b)
      return KIND_ORDER[na.kind] - KIND_ORDER[nb.kind] || (na.lineno ?? 0) - (nb.lineno ?? 0) || na.name.localeCompare(nb.name)
    })
  }
  return { nodes, children, edges: data.edges, root: data.root ?? 'dir:' }
}

export function hasChildren(index, id) {
  return (index.children.get(id) || []).length > 0
}

// 폴더 하나만 들어 있는 폴더(src/ 같은)는 자동으로 펼친다
function expandSingleChains(index, expanded, id) {
  let cur = id
  for (;;) {
    const kids = index.children.get(cur) || []
    if (kids.length !== 1 || index.nodes.get(kids[0]).kind !== 'folder') return
    cur = kids[0]
    expanded.add(cur)
  }
}

// 지금 보이지만 접혀 있는 폴더들
function collapsedFolders(index, expanded) {
  const out = []
  const walk = (id) => {
    for (const c of index.children.get(id) || []) {
      if (index.nodes.get(c).kind !== 'folder') continue
      if (expanded.has(c)) walk(c)
      else out.push(c)
    }
  }
  walk(index.root)
  return out
}

export function expandedForLevel(index, level) {
  const expanded = new Set([index.root])
  expandSingleChains(index, expanded, index.root)
  if (level === 'folder') {
    // src/ 와 setup.py 처럼 보이는 게 너무 적으면, 큰 폴더부터 한 단계씩 더 펼친다
    for (let guard = 0; guard < 30 && countVisible(index, expanded) < 6; guard += 1) {
      const cands = collapsedFolders(index, expanded)
      if (cands.length === 0) break
      cands.sort((x, y) => (index.children.get(y) || []).length - (index.children.get(x) || []).length)
      expanded.add(cands[0])
    }
    return expanded
  }
  for (const n of index.nodes.values()) {
    if (n.kind === 'folder') expanded.add(n.id)
    if (level === 'function' && (n.kind === 'file' || n.kind === 'class')) expanded.add(n.id)
  }
  return expanded
}

export function countVisible(index, expanded) {
  let count = 0
  const walk = (id) => {
    for (const c of index.children.get(id) || []) {
      count += 1
      if (expanded.has(c)) walk(c)
    }
  }
  walk(index.root)
  return count
}

// 노드들이 보이도록 조상을 모두 펼친 새 expanded 집합
export function revealIds(index, expanded, ids) {
  const next = new Set(expanded)
  for (const id of ids) {
    let cur = index.nodes.get(id)?.parent
    while (cur != null) {
      next.add(cur)
      cur = index.nodes.get(cur)?.parent
    }
  }
  return next
}

export function visibleGraph(index, expanded, edgeKinds) {
  const visible = new Set()
  const vparent = new Map()
  const walk = (id, parent) => {
    for (const c of index.children.get(id) || []) {
      visible.add(c)
      vparent.set(c, parent)
      if (expanded.has(c) && hasChildren(index, c)) walk(c, c)
    }
  }
  walk(index.root, null)

  const repCache = new Map()
  const rep = (id) => {
    if (repCache.has(id)) return repCache.get(id)
    let cur = id
    while (cur != null && !visible.has(cur)) cur = index.nodes.get(cur)?.parent ?? null
    repCache.set(id, cur)
    return cur
  }
  const isAncestor = (a, b) => {
    let cur = vparent.get(b)
    while (cur) {
      if (cur === a) return true
      cur = vparent.get(cur)
    }
    return false
  }

  const merged = new Map()
  for (const e of index.edges) {
    if (edgeKinds && !edgeKinds.has(e.kind)) continue
    const s = rep(e.source)
    const t = rep(e.target)
    if (!s || !t || s === t || isAncestor(s, t) || isAncestor(t, s)) continue
    const key = `${s}|${t}|${e.kind}`
    const cur = merged.get(key)
    if (cur) {
      cur.weight += e.weight
      cur.count += 1
      cur.heuristic = cur.heuristic && e.heuristic
    } else {
      merged.set(key, { id: key, source: s, target: t, kind: e.kind, weight: e.weight, count: 1, heuristic: e.heuristic })
    }
  }

  const nodes = [...visible].map((id) => ({
    ...index.nodes.get(id),
    vparent: vparent.get(id),
    compound: expanded.has(id) && hasChildren(index, id),
    childCount: (index.children.get(id) || []).length,
  }))
  return { nodes, edges: [...merged.values()], rep }
}
