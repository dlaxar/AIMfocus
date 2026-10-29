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
