// Core API root. Override with VITE_BASE_URL in frontend/.env.local; '/api' goes through the Vite dev proxy.
export const BASE_URL = (import.meta.env.VITE_BASE_URL ?? '/api').replace(/\/$/, '')

// Searches patients by name or ID. Returns array of { id, name, dob, ward }.
export async function searchPatients(q, limit = 10) {
  const url = `${BASE_URL}/v2/patients/search?q=${encodeURIComponent(q)}&limit=${limit}`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`Patient search failed (${res.status})`)
  return res.json()
}

// Creates a session (a core screen) for the given situation and returns its ID.
// The core answers at once and keeps filling the screen in the background.
export async function createSession(situation) {
  const res = await fetch(`${BASE_URL}/v2/screens`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ situation }),
  })
  if (!res.ok) throw new Error(`Session request failed (${res.status})`)
  const { screen_id } = await res.json()
  if (!screen_id) throw new Error('Session response did not contain an ID')
  return String(screen_id)
}

// Current screen of a session: { status: 'partial'|'complete', role, task, variables[], contradictions[] }.
export async function getScreen(sessionId) {
  const res = await fetch(`${BASE_URL}/v2/screens/${encodeURIComponent(sessionId)}`)
  if (!res.ok) throw new Error(`Screen request failed (${res.status})`)
  return res.json()
}

// Fetches a short 'Role, Situation' summary label + icon URL for a session from the LLM.
// Returns { summary: string, icon_url: string }
export async function getSessionSummary(sessionId) {
  const res = await fetch(`${BASE_URL}/v2/screens/${encodeURIComponent(sessionId)}/summary`)
  if (!res.ok) throw new Error(`Summary request failed (${res.status})`)
  const data = await res.json()
  return { summary: data.summary ?? '', icon_url: data.icon_url ?? '' }
}

// Live updates while the core searches the documents for missing items.
// Calls onScreen(screen) for every update; returns a function that stops listening.
export function watchScreen(sessionId, onScreen, onError) {
  const source = new EventSource(`${BASE_URL}/v2/screens/${encodeURIComponent(sessionId)}/stream`)
  source.addEventListener('screen', (e) => onScreen(JSON.parse(e.data)))
  source.addEventListener('done', () => source.close())
  source.onerror = () => {
    source.close()
    onError?.()
  }
  return () => source.close()
}

// Passage → { doc: {doc_id, title, date, type, department}, passage, section: {name, start, end, text} }.
// Cached per passage: the same source is opened many times on one screen.
const passageContexts = new Map()
export function getPassageContext(passageId) {
  if (!passageContexts.has(passageId)) {
    const p = fetch(`${BASE_URL}/passages/${encodeURIComponent(passageId)}/context`).then((res) => {
      if (!res.ok) throw new Error(`Source request failed (${res.status})`)
      return res.json()
    })
    p.catch(() => passageContexts.delete(passageId))
    passageContexts.set(passageId, p)
  }
  return passageContexts.get(passageId)
}

// Whole letter: { doc_id, title, date, type, department, text, passages[] }. Cached per document.
const documents = new Map()
export function getDocument(docId) {
  if (!documents.has(docId)) {
    const p = fetch(`${BASE_URL}/documents/${encodeURIComponent(docId)}`).then((res) => {
      if (!res.ok) throw new Error(`Document request failed (${res.status})`)
      return res.json()
    })
    p.catch(() => documents.delete(docId))
    documents.set(docId, p)
  }
  return documents.get(docId)
}
