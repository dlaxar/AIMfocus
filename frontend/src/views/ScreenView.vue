<template>
  <header>
    <h1>{{ screen?.role ? capitalize(screen.role) : 'Patient facts' }}</h1>
    <p v-if="screen?.task" class="task">{{ screen.task }}</p>
    <p v-if="screen?.reference_date" class="muted">As of {{ screen.reference_date }}</p>
  </header>

  <p v-if="error" class="error" role="alert">{{ error }}</p>
  <p v-else-if="!screen" class="muted">Loading…</p>

  <ul v-if="screen" class="vars">
    <li v-for="v in screen.variables" :key="v.name" :class="['var', v.state]">
      <div class="head">
        <span class="label">{{ v.label }}</span>
        <span class="badge">{{ stateLabel[v.state] ?? v.state }}</span>
      </div>
      <p class="value">{{ v.state === 'pending' ? 'Searching the documents…' : v.value }}</p>
      <ul v-if="v.sources.length" class="sources">
        <li v-for="s in v.sources" :key="s.passage_id">
          <span class="doc">{{ s.doc_title }}, {{ s.doc_date }}</span>
          <q>{{ s.quote_de }}</q>
        </li>
      </ul>
      <p v-if="v.note && v.state !== 'documented'" class="note">{{ v.note }}</p>
    </li>
  </ul>

  <section v-if="screen?.contradictions?.length" class="contradictions">
    <h2>Contradictions in the documents</h2>
    <ul>
      <li v-for="c in screen.contradictions" :key="c.contradiction_id">{{ c.summary_en }}</li>
    </ul>
  </section>

  <p v-if="screen?.status === 'complete' && screen.timings_ms" class="muted timing">
    First answer {{ secs(screen.timings_ms.first_response) }} s · complete {{ secs(screen.timings_ms.complete) }} s
  </p>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { getScreen, watchScreen } from '../api.js'
import { session } from '../store.js'

const screen = ref(null)
const error = ref('')
let stop = null

const stateLabel = {
  documented: 'documented',
  derived: 'from text',
  pending: 'searching',
  not_documented: 'not documented',
  check: 'check',
}

const capitalize = (s) => s.charAt(0).toUpperCase() + s.slice(1)
const secs = (ms) => (ms == null ? '–' : (ms / 1000).toFixed(1))

onMounted(async () => {
  if (!session.currentId) {
    error.value = 'No active session – start on the first screen.'
    return
  }
  try {
    screen.value = await getScreen(session.currentId)
    if (screen.value.status !== 'complete') {
      stop = watchScreen(session.currentId, (s) => (screen.value = s))
    }
  } catch (e) {
    error.value = e.message || 'Could not load the screen'
  }
})

onBeforeUnmount(() => stop?.())
</script>

<style scoped>
header { display: flex; flex-direction: column; gap: 4px; text-align: center; }
.task { margin: 0; font-size: 1.1rem; }
.muted { margin: 0; color: var(--muted); font-size: 0.9rem; }
.error { color: #ce2c31; }
.vars, .sources, .contradictions ul { list-style: none; margin: 0; padding: 0; }
.vars { display: flex; flex-direction: column; gap: 12px; }
.var { background: var(--surface); border: 1.5px solid var(--border); border-radius: 12px; padding: 12px 14px; }
.var.not_documented { border-style: dashed; }
.var.check { border-color: #f5a623; }
.head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
.label { font-weight: 600; }
.badge { font-size: 0.75rem; color: var(--muted); white-space: nowrap; }
.var.pending .badge { color: var(--accent); }
.value { margin: 6px 0 0; }
.var.pending .value, .var.not_documented .value { color: var(--muted); }
.sources { margin-top: 8px; display: flex; flex-direction: column; gap: 6px; font-size: 0.85rem; }
.doc { display: block; color: var(--muted); }
q { font-style: italic; }
.note { margin: 6px 0 0; font-size: 0.8rem; color: var(--muted); }
.contradictions h2 { font-size: 1.1rem; margin: 0 0 8px; }
.contradictions li { background: #fff4e5; border-radius: 8px; padding: 8px 10px; margin-bottom: 6px; font-size: 0.9rem; }
.timing { text-align: center; }
</style>
