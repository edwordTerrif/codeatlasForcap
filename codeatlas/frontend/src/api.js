// 백엔드(FastAPI) 호출 모음. 개발 중에는 Vite가 /api를 localhost:8000으로 넘겨 준다.

async function request(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  let body = null
  try {
    body = await res.json()
  } catch {
    body = null
  }
  if (!res.ok) {
    const detail = body && (typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail))
    throw new Error(detail || `요청 실패 (${res.status})`)
  }
  return body
}

export const api = {
  health: () => request('/health'),
  projects: () => request('/projects'),
  analyze: (source, includeTests) =>
    request('/projects', { method: 'POST', body: JSON.stringify({ source, include_tests: includeTests }) }),
  status: (pid) => request(`/projects/${encodeURIComponent(pid)}/status`),
  graph: (pid) => request(`/projects/${encodeURIComponent(pid)}/graph`),
  node: (pid, id) => request(`/projects/${encodeURIComponent(pid)}/node?id=${encodeURIComponent(id)}`),
  ask: (pid, question) =>
    request(`/projects/${encodeURIComponent(pid)}/ask`, { method: 'POST', body: JSON.stringify({ question }) }),
  tour: (pid) => request(`/projects/${encodeURIComponent(pid)}/tour`),
}
