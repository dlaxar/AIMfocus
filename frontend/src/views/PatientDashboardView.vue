<template>
  <div class="dashboard">
    <!-- ── Patient header ─────────────────────────── -->
    <header class="patient-header">
      <button class="back-btn" type="button" @click="router.back()" aria-label="Back">
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <path d="M15 18l-6-6 6-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>
      <h1 class="patient-name">{{ patient?.name ?? 'Anna Musterfrau' }}</h1>
    </header>

    <!-- ── Fact cards (scrollable body) ──────────── -->
    <div class="facts-scroll">
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <p v-else-if="!screen" class="empty-state muted">Loading…</p>

      <div v-else class="facts-grid">
        <FactCard
          v-for="v in screen.variables"
          :key="v.name"
          :label="v.label"
          :value="v.state === 'pending' ? null : v.value"
          :class="['fact-card-wrap', v.state]"
        >
          <span class="badge">{{ stateLabel[v.state] ?? v.state }}</span>
          <ul v-if="v.sources && v.sources.length" class="sources">
            <li v-for="s in v.sources" :key="s.passage_id">
              <span class="doc">{{ s.doc_title }}, {{ s.doc_date }}</span>
              <q>{{ s.quote_de }}</q>
            </li>
          </ul>
          <p v-if="v.note && v.state !== 'documented'" class="note">{{ v.note }}</p>
        </FactCard>
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
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import FactCard from '../components/FactCard.vue'
import { getScreen, watchScreen } from '../api.js'
import { session } from '../store.js'

const router = useRouter()

// ── Screen / facts ────────────────────────────────────────────────────────────
const screen = ref(null)
const error  = ref('')
let stop = null

const stateLabel = {
  documented:     'documented',
  derived:        'from text',
  pending:        'searching',
  not_documented: 'not documented',
  check:          'check',
}

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
 * The dashboard needs to escape the 420px .screen wrapper on desktop.
 * We use a negative margin trick to cover the full viewport width,
 * then re-centre content with an inner max-width on larger screens.
 */
.dashboard {
  display: flex;
  flex-direction: column;
  /* Break out of .screen's padding/max-width */
  margin: -48px -24px -32px;
  min-height: 100dvh;
  background: var(--bg);
  /* On desktop the .screen cap is irrelevant once we're full-width */
  width: 100vw;
  position: relative;
  left: 50%;
  right: 50%;
  margin-left: -50vw;
  margin-right: -50vw;
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

/* Fade band below the header — cards dissolve out of the background */
.patient-header::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  top: 100%;
  height: 56px;
  pointer-events: none;
  background: linear-gradient(
    to bottom,
    var(--bg) 0%,
    transparent 100%
  );
  z-index: 9;
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

.patient-name {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ── Scrollable facts area ──────────────────────────── */
.facts-scroll {
  flex: 1;
  overflow-y: auto;
  /* Extra bottom padding so last card isn't hidden behind the fixed footer */
  padding: 20px 24px 120px;
}

.facts-grid {
  display: grid;
  /* Mobile: single column. Auto-fill wider cards on desktop. */
  grid-template-columns: 1fr;
  gap: 12px;
}

@media (min-width: 600px) {
  .facts-grid {
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  }
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

/* ── Screen variable extras ─────────────────────────── */
.error { color: #ce2c31; padding: 0 24px; }
.muted { color: var(--muted); }

.badge {
  display: block;
  font-size: 0.72rem;
  color: var(--muted);
  margin-top: 2px;
}
:deep(.fact-card-wrap.pending) .badge { color: var(--accent); }
:deep(.fact-card-wrap.not_documented) { border-style: dashed; }
:deep(.fact-card-wrap.check) { border-color: #f5a623; }

.sources {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 0.85rem;
}
.doc { display: block; color: var(--muted); }
q { font-style: italic; }
.note { margin: 6px 0 0; font-size: 0.8rem; color: var(--muted); }
</style>
