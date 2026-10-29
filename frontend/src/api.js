// Backend root. Override with VITE_BASE_URL in frontend/.env.local; '/api' goes through the Vite dev proxy.
export const BASE_URL = (import.meta.env.VITE_BASE_URL ?? '/api').replace(/\/$/, '')

// UTF-8 safe base64 (btoa alone breaks on umlauts etc.)
function toBase64(text) {
  const bytes = new TextEncoder().encode(text)
  let binary = ''
  for (const b of bytes) binary += String.fromCharCode(b)
  return btoa(binary)
}

// Searches patients by name or ID. Returns array of { id, name, dob, ward }.
export async function searchPatients(q, limit = 10) {
  const url = `${BASE_URL}/patients/search?q=${encodeURIComponent(q)}&limit=${limit}`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`Patient search failed (${res.status})`)
  return res.json()
}

// Creates a session for the given situation and returns its ID.
export async function createSession(situation) {
  const url = `${BASE_URL}/session/get?situation=${encodeURIComponent(toBase64(situation))}`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`Session request failed (${res.status})`)

  // Accept either a bare ID or JSON like {"session_id": "..."} / {"id": "..."}
  const body = (await res.text()).trim()
  try {
    const data = JSON.parse(body)
    if (typeof data === 'string' || typeof data === 'number') return String(data)
    const id = data.session_id ?? data.sessionId ?? data.id
    if (id != null) return String(id)
  } catch {
    if (body) return body
  }
  throw new Error('Session response did not contain an ID')
}

// Current screen of a session: { status: 'partial'|'complete', role, task, variables[], contradictions[] }.
export async function getScreen(sessionId) {
  const res = await fetch(`${BASE_URL}/session/${encodeURIComponent(sessionId)}`)
  if (!res.ok) throw new Error(`Screen request failed (${res.status})`)
  return res.json()
}

// Fetches a short 'Role, Situation' summary label for a session from the LLM.
export async function getSessionSummary(sessionId) {
  const res = await fetch(`${BASE_URL}/session/${encodeURIComponent(sessionId)}/summary`)
  if (!res.ok) throw new Error(`Summary request failed (${res.status})`)
  const data = await res.json()
  return data.summary ?? ''
}

// Live updates while the backend searches the documents for missing items.
// Calls onScreen(screen) for every update; returns a function that stops listening.
export function watchScreen(sessionId, onScreen, onError) {
  const source = new EventSource(`${BASE_URL}/session/${encodeURIComponent(sessionId)}/stream`)
  source.addEventListener('screen', (e) => onScreen(JSON.parse(e.data)))
  source.addEventListener('done', () => source.close())
  source.onerror = () => {
    source.close()
    onError?.()
  }
  return () => source.close()
}
