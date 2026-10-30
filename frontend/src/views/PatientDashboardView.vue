<template>
  <div class="dashboard">
    <!-- ── Patient header ─────────────────────────── -->
    <header class="patient-header">
      <button class="back-btn" type="button" @click="router.back()" aria-label="Back">
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <path d="M15 18l-6-6 6-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>
      <img src="../assets/logo.png" alt="Logo" class="header-logo" />
    </header>

    <!-- ── Screen (scrollable body) ──────────────── -->
    <div class="facts-scroll">
      <div class="screen-body">
        <!-- Patient and situation -->
        <div class="patient-info">
          <h1 v-if="patient" class="patient-name">{{ patient.name }}</h1>
          <div v-if="patient" class="patient-meta">
            <span v-for="(m, i) in patientMeta" :key="i">{{ m }}</span>
          </div>
          <p v-if="screen" class="situation">
            <strong v-if="screen.role">{{ screen.role }}</strong>
            <span v-if="screen.task"> — {{ screen.task }}</span>
            <span v-if="screen.reference_date" class="as-of">as of {{ formatDate(screen.reference_date) }}</span>
            <span v-if="screen.status !== 'complete'" class="live">
              <span class="live-dot" aria-hidden="true" />searching the letters…
            </span>
          </p>
        </div>

        <p v-if="error" class="error" role="alert">{{ error }}</p>
        <p v-else-if="!screen" class="empty-state muted">Loading…</p>
        <p v-else-if="screen.error" class="error" role="alert">{{ screen.error }}</p>

        <p v-if="screen?.contradictions?.length" class="cx-banner">
          <ContradictionBadge :ids="screen.contradictions.map((c) => c.contradiction_id)" />
          {{ screen.contradictions.length }} contradiction{{ screen.contradictions.length > 1 ? 's' : '' }}
          between the letters affect{{ screen.contradictions.length > 1 ? '' : 's' }} this screen; the values involved are marked.
        </p>

        <!-- Tiers, top to bottom -->
        <section v-for="t in tiers" :key="t.tier" class="tier" :class="`tier-${t.tier}`">
          <header class="tier-head">
            <h2>{{ TIER_LABEL[t.tier] }}</h2>
            <span class="tier-hint">{{ TIER_HINT[t.tier] }}</span>
            <button
              v-if="t.tier === 'background'"
              type="button"
              class="tier-toggle"
              :aria-expanded="backgroundOpen"
              @click="backgroundOpen = !backgroundOpen"
            >
              {{ backgroundOpen ? 'Show summary' : 'Show details' }}
            </button>
          </header>

          <!-- Collapsed: one headline per variable -->
          <table v-if="t.tier === 'background' && !backgroundOpen" class="fact-table value-table">
            <colgroup><col class="col-label" /><col /><col class="col-date" /><col class="col-icons" /></colgroup>
            <tbody>
              <tr v-for="v in t.variables" :key="v.name" :class="{ 'has-cx': v.contradictions?.length }">
                <th scope="row" class="label-cell"><span class="var-label">{{ v.label }}</span></th>
                <td>
                  <span v-if="v.state === 'pending'" class="skeleton" />
                  <span v-else :class="{ missing: v.state === 'not_documented' || v.state === 'not_available' }">{{ v.headline?.text ?? v.value }}</span>
                  <span v-if="v.headline?.change" class="date-cell"> · {{ v.headline.change }}</span>
                </td>
                <td class="date-cell">{{ formatDate(v.headline?.date) }}</td>
                <td class="icon-cell">
                  <ContradictionBadge :refs="v.contradictions ?? []" />
                  <SourceButton v-if="v.sources?.length" :sources="v.sources" />
                </td>
              </tr>
            </tbody>
          </table>

          <template v-else>
            <div v-for="b in t.blocks" :key="b.key" class="block" :class="`block-${b.kind}`">
              <component :is="BLOCKS[b.kind]" :variables="b.variables" />
            </div>
          </template>
        </section>

        <!-- Contradictions elsewhere in the record -->
        <details v-if="screen?.other_contradictions?.length" class="other-cx">
          <summary>
            {{ screen.other_contradictions.length }} other contradiction{{ screen.other_contradictions.length > 1 ? 's' : '' }} in the record
          </summary>
          <ul>
            <li v-for="cx in screen.other_contradictions" :key="cx.contradiction_id">
              <ContradictionBadge :ids="[cx.contradiction_id]" />
              <span>{{ cx.summary_en }}</span>
            </li>
          </ul>
        </details>

        <!-- ── Timings ──────────────────────────────────── -->
        <div v-if="screen?.timings_ms" class="timings">
          <span class="timings-label">Timings</span>
          <dl class="timings-grid">
            <template v-for="(val, key) in screen.timings_ms" :key="key">
              <dt>{{ key.replace(/_/g, ' ') }}</dt>
              <dd>{{ val.toFixed(1) }} ms</dd>
            </template>
          </dl>
        </div>
      </div>
    </div>

    <!-- ── Chat input (sticky bottom) ────────────── -->
    <div class="chat-footer">
      <div class="chat-input-row">
        <textarea
          v-model="chatMessage"
          class="chat-input"
          placeholder="Ask something about this patient…"
          rows="1"
          @keydown.enter.exact.prevent="sendMessage"
          @input="autoResize"
          ref="textareaEl"
        />
        <button
          class="send-btn"
          type="button"
          :disabled="!chatMessage.trim()"
          @click="sendMessage"
        >
          <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
            <path d="M22 2L11 13M22 2L15 22l-4-9-9-4 20-7z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </button>
      </div>
    </div>

    <DocumentDrawer />
  </div>
</template>

<script setup>
import { ref, computed, provide, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import ContradictionBadge from '../components/screen/ContradictionBadge.vue'
import SourceButton from '../components/screen/SourceButton.vue'
import DocumentDrawer from '../components/screen/DocumentDrawer.vue'
import ValueTable from '../components/screen/ValueTable.vue'
import DataTable from '../components/screen/DataTable.vue'
import ListGroup from '../components/screen/ListGroup.vue'
import TrendGroup from '../components/screen/TrendGroup.vue'
import { groupByTier } from '../components/screen/layout.js'
import { formatDate } from '../components/screen/format.js'
import '../components/screen/screen.css'
import { getScreen, watchScreen } from '../api.js'
import { session, form } from '../store.js'

const router = useRouter()
const patient = computed(() => form.patientData)
const patientMeta = computed(() => {
  const p = patient.value
  if (!p) return []
  return [
    p.sex,
    p.age != null ? `${p.age} y` : null,
    p.dateOfBirth ? `born ${formatDate(p.dateOfBirth)}` : null,
    p.id,
  ].filter(Boolean)
})

// ── Screen / facts ────────────────────────────────────────────────────────────
const screen = ref(null)
const error  = ref('')
let stop = null

const TIER_LABEL = { critical: 'Critical', important: 'Important', background: 'Background' }
const TIER_HINT = {
  critical: 'check before acting',
  important: 'for this task',
  background: 'context',
}
const BLOCKS = { values: ValueTable, tables: DataTable, lists: ListGroup, trends: TrendGroup }

const tiers = computed(() => (screen.value ? groupByTier(screen.value.variables ?? []) : []))
const backgroundOpen = ref(false)

// Every contradiction in the record by ID, for the badges on values, rows and points
const contradictionIndex = computed(() => new Map(
  [...(screen.value?.other_contradictions ?? []), ...(screen.value?.contradictions ?? [])]
    .map((c) => [c.contradiction_id, c]),
))
provide('contradictionIndex', contradictionIndex)

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

// ── Chat ──────────────────────────────────────────────────────────────────────
const chatMessage = ref('')
const textareaEl  = ref(null)

const emit = defineEmits(['send'])

function sendMessage() {
  const text = chatMessage.value.trim()
  if (!text) return
  emit('send', text)
  chatMessage.value = ''
  if (textareaEl.value) {
    textareaEl.value.style.height = 'auto'
  }
}

function autoResize(e) {
  const el = e.target
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 160) + 'px'
}
</script>

<style scoped>
/* ── Layout ─────────────────────────────────────────── */
/*
 * The dashboard uses the whole viewport: on this route the .screen wrapper drops its
 * column width and padding, and the content re-centres with .screen-body's max-width.
 */
:global(.screen:has(> .dashboard)) {
  max-width: none;
  padding: 0;
}

.dashboard {
  display: flex;
  flex-direction: column;
  min-height: 100dvh;
  background: var(--bg);
}

/* ── Header ─────────────────────────────────────────── */
.patient-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 24px 16px;
  /* Solid bg so the sticky header covers scrolling content all the way to the top */
  background: var(--bg);
  border-bottom: none;
  position: sticky;
  top: 0;
  z-index: 10;
}

.back-btn {
  flex-shrink: 0;
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  padding: 0;
  transition: color 0.15s;
}
.back-btn:hover {
  color: var(--text);
}

.header-logo {
  height: 32px;
  width: auto;
}

/* ── Patient info banner ──────────────────────────── */
.patient-info {
  margin-bottom: 18px;
}

.patient-name {
  margin: 0 0 4px;
  text-align: left;
  font-size: 1.6rem;
  font-weight: 600;
}

.patient-meta {
  font-size: 0.875rem;
  color: var(--muted);
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.patient-meta span + span::before {
  content: '·';
  margin-right: 4px;
  color: var(--border);
}

.patient-id {
  font-family: monospace;
  font-size: 0.82rem;
}

/* ── Scrollable facts area ──────────────────────────── */
.facts-scroll {
  flex: 1;
  overflow-y: auto;
  /* Extra bottom padding so last card isn't hidden behind the fixed footer */
  padding: 20px 24px 120px;
}

.screen-body {
  max-width: 880px;
  margin: 0 auto;
}

@media (max-width: 600px) {
  .facts-scroll {
    padding-left: 16px;
    padding-right: 16px;
  }
  .tier {
    padding-left: 10px;
  }
}

.situation {
  margin: 10px 0 0;
  font-size: 0.92rem;
  line-height: 1.45;
}
.as-of {
  margin-left: 8px;
  font-size: 0.8rem;
  color: var(--muted);
}
.live {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: 10px;
  font-size: 0.8rem;
  color: var(--accent);
}
.live-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
  animation: pulse 1.2s ease-in-out infinite;
}
@keyframes pulse {
  50% { opacity: 0.3; }
}

.cx-banner {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 16px;
  padding: 6px 10px 6px 6px;
  border-radius: 8px;
  background: var(--cx-bg);
  border: 1px solid #f2b8b5;
  font-size: 0.84rem;
}

/* ── Tiers ──────────────────────────────────────────── */
.tier {
  margin-bottom: 22px;
  padding: 2px 0 2px 14px;
  border-left: 3px solid var(--border);
}
.tier-critical {
  border-left-color: var(--cx);
}
.tier-important {
  border-left-color: var(--accent);
}
.tier-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 6px;
}
.tier-head h2 {
  margin: 0;
  font-size: 0.74rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.tier-critical .tier-head h2 {
  color: var(--cx);
}
.tier-hint {
  font-size: 0.76rem;
  color: var(--muted);
}
.tier-toggle {
  margin-left: auto;
  border: none;
  background: none;
  padding: 0;
  font: inherit;
  font-size: 0.78rem;
  color: var(--accent);
  cursor: pointer;
}
.tier-toggle:hover {
  text-decoration: underline;
}
.block + .block {
  margin-top: 14px;
}

.other-cx {
  margin-top: 8px;
  font-size: 0.84rem;
}
.other-cx summary {
  cursor: pointer;
  color: var(--muted);
}
.other-cx ul {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.other-cx li {
  display: flex;
  align-items: flex-start;
  gap: 6px;
}

.empty-state {
  margin: 48px 0 0;
  text-align: center;
  color: var(--muted);
  font-size: 0.9rem;
}

/* ── Chat footer ────────────────────────────────────── */
.chat-footer {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  padding: 12px 24px 20px;
  /* No background or border — the fade pseudo-element handles the transition */
  background: transparent;
  border: none;
  z-index: 20;
}

/* Frosted fade above the input — fades the card content into the page background */
.chat-footer::before {
  content: '';
  position: absolute;
  inset: 0;
  bottom: 0;
  top: -56px;          /* how tall the fade band is */
  pointer-events: none;
  background: linear-gradient(
    to bottom,
    transparent 0%,
    var(--bg) 60%
  );
  z-index: -1;
}

.chat-input-row {
  display: flex;
  align-items: flex-end;
  gap: 10px;
}

.chat-input {
  flex: 1;
  padding: 12px 14px;
  border-radius: 16px;
  resize: none;
  line-height: 1.45;
  max-height: 160px;
  overflow-y: auto;
  /* override global textarea border-radius just for this field */
  border-radius: 16px;
}

.send-btn {
  flex-shrink: 0;
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  border: none;
  border-radius: 50%;
  background: var(--accent);
  color: #fff;
  cursor: pointer;
  padding: 0;
  transition: opacity 0.15s;
}
.send-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}
.send-btn:not(:disabled):hover {
  opacity: 0.85;
}

.error { color: #ce2c31; }
.muted { color: var(--muted); }

/* ── Timings ────────────────────────────────────────── */
.timings {
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
}

.timings-label {
  display: block;
  font-size: 0.72rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
  margin-bottom: 8px;
}

.timings-grid {
  display: grid;
  grid-template-columns: auto 1fr;
  column-gap: 16px;
  row-gap: 4px;
  margin: 0;
}

.timings-grid dt {
  font-size: 0.82rem;
  color: var(--muted);
  text-transform: capitalize;
}

.timings-grid dd {
  font-size: 0.82rem;
  font-family: monospace;
  color: var(--text);
  margin: 0;
}
</style>
