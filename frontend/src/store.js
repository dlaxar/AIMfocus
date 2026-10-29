import { reactive, watch } from 'vue'

// Answers collected across the screens. Swap for a POST to /api/... once the backend exists.
export const form = reactive({
  iAm: '',
  patient: '',
})

const CURRENT_KEY = 'currentSessionId'
const RECENT_KEY = 'recentSessions'
const MAX_RECENT = 10

function load(key, fallback) {
  try {
    const raw = localStorage.getItem(key)
    return raw == null ? fallback : JSON.parse(raw)
  } catch {
    return fallback
  }
}

function save(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // Storage unavailable (private mode etc.) – keep in memory only
  }
}

// The active session (read this in later views) plus recently created ones, persisted across reloads.
export const session = reactive({
  currentId: load(CURRENT_KEY, null),
  recent: load(RECENT_KEY, []), // [{ id, title, createdAt }]
})

watch(() => session.currentId, (id) => save(CURRENT_KEY, id))
watch(() => session.recent, (list) => save(RECENT_KEY, list), { deep: true })

export function startSession(id, situation) {
  const title = situation.trim().replace(/\s+/g, ' ')
  session.currentId = id
  session.recent = [
    { id, title: title.length > 60 ? title.slice(0, 57) + '…' : title, situation, summary: null, createdAt: Date.now() },
    ...session.recent.filter((s) => s.id !== id),
  ].slice(0, MAX_RECENT)
}

export function setSessionSummary(id, summary) {
  const entry = session.recent.find((s) => s.id === id)
  if (entry) entry.summary = summary
}

export function resumeSession(id) {
  const entry = session.recent.find((s) => s.id === id)
  session.currentId = id
  if (entry?.situation) form.iAm = entry.situation
}
