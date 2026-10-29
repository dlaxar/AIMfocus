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
      <div class="facts-grid">
        <FactCard
          v-for="fact in facts"
          :key="fact.key"
          :label="fact.label"
          :value="fact.value"
        />
      </div>

      <!-- Drop an empty state while there are no facts yet -->
      <p v-if="facts.length === 0" class="empty-state">
        No information loaded yet.
      </p>
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
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import FactCard from '../components/FactCard.vue'
import { form } from '../store.js'

const router = useRouter()

// ── Patient data ──────────────────────────────────────────────────────────────
// The patient object is passed as a route state (set in PatientView when navigating here).
// Falls back to whatever is in the store if navigation state is absent.
const patient = ref(history.state?.patient ?? null)

// ── Fact cards ────────────────────────────────────────────────────────────────
// Each entry: { key: string, label: string, value: string | null }
// Add, remove or reorder entries here to customise the displayed facts.
const facts = ref([
  { key: 'dob',        label: 'Date of birth', value: patient.value?.dob   ?? null },
  { key: 'ward',       label: 'Ward',           value: patient.value?.ward  ?? null },
  { key: 'id',         label: 'Patient ID',     value: patient.value?.id    ?? null },
  { key: 'diagnosis',  label: 'Diagnosis',      value: null },
  { key: 'allergies',  label: 'Allergies',      value: null },
  { key: 'medication', label: 'Medication',     value: null },
])

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
</style>
