<template>
  <div class="logo-wrap">
    <AIMfocusLogo />
  </div>

  <h1>What's your role, and what do you need?</h1>

  <div class="box">
    <textarea v-model="form.iAm" placeholder="I am... and I want..." />
    <button
      class="mic"
      :class="{ listening }"
      type="button"
      :disabled="!speechSupported"
      :title="speechSupported ? (listening ? 'Stop' : 'Speak') : 'Speech input not supported in this browser'"
      @click="toggleMic"
    >
      <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
        <path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Zm-7 9a7 7 0 0 0 14 0M12 19v3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
      </svg>
    </button>
  </div>

  <p class="tip" style="padding-bottom: 20px" @click="form.iAm = tips[tipIndex]" @mouseenter="tipPaused = true" @mouseleave="tipPaused = false">
    <strong>Try: </strong>
    <Transition name="fade" mode="out-in">
      <span :key="tipIndex">"{{ tips[tipIndex] }}"</span>
    </Transition>
  </p>

  <section v-if="session.recent.length" class="recent">
    <h2>Recent</h2>
    <ul>
      <li v-for="s in session.recent" :key="s.id">
        <button type="button" :class="{ active: s.id === session.currentId }" @click="resume(s.id)">
          <span class="title">{{ s.title }}</span>
          <span class="date">{{ formatDate(s.createdAt) }}</span>
        </button>
      </li>
    </ul>
  </section>

  <p v-if="error" class="error" role="alert">{{ error }}</p>

  <Teleport to="body">
    <NextButton :disabled="!form.iAm.trim() || loading" @click="submit" />
  </Teleport>
</template>

<script setup>
import { ref, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import NextButton from '../components/NextButton.vue'
import AIMfocusLogo from '../components/AIMfocusLogo.vue'
import { form, session, startSession, resumeSession } from '../store.js'
import { createSession } from '../api.js'

const router = useRouter()

const loading = ref(false)
const error = ref('')
async function submit() {
  const situation = form.iAm.trim()
  if (!situation || loading.value) return
  loading.value = true
  error.value = ''
  try {
    startSession(await createSession(situation), situation)
    router.push('/patient')
  } catch (e) {
    error.value = e.message || 'Could not start a session'
  } finally {
    loading.value = false
  }
}

function resume(id) {
  resumeSession(id)
  router.push('/patient')
}

// Example prompts, shuffled per visit so each one starts somewhere new. Rotates unless hovered.
const tips = shuffle([
  'I am the anaesthetist and need a pre-op check on patients having quadrantectomy',
  'I am the surgeon planning an operation after a tumour board',
  'I am the GP and want to review diabetes therapy',
  'I am the emergency physician on night duty',
  'I am the GP and want to check which follow-ups are still open',
  'I am the cardiologist and want to see how heart function has changed over time',
  'I am the oncologist and need the tumour stage and receptor status before planning therapy',
  'I am the radiologist and need to know about contrast allergies and kidney function before a CT',
  'I am the ward pharmacist and want to check the current medication for interactions',
  'I am the neurologist and want to review previous strokes or TIAs and their work-up',
  'I am the nurse on admission and need allergies, mobility and current medication',
  'I am the resident doing the morning round and want a summary of the last 24 hours',
  'I am the discharging physician and need the open issues for the discharge letter',
  'I am the nephrologist and want to see the course of creatinine and eGFR',
  'I am the endocrinologist and want the HbA1c trend and glucose-lowering drugs',
  'I am the vascular surgeon and want all carotid ultrasound findings',
  'I am the physiotherapist and need restrictions and precautions before mobilisation',
  'I am the psychiatrist on consult and want current psychotropic medication and past episodes',
  'I am the radiation oncologist and need prior radiotherapy and the current treatment plan',
  'I am the on-call doctor and need to know whether anticoagulants have to be paused before a procedure',
  'I am the dietitian and want the weight course and nutrition-related diagnoses',
  'I am the palliative care physician and want the advance directive and goals of care',
  'I am the infectious disease consultant and want recent cultures and antibiotic history',
  'I am the GP taking over this patient and want a short overview of the medical history',
])
function shuffle(list) {
  for (let i = list.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[list[i], list[j]] = [list[j], list[i]]
  }
  return list
}
const tipIndex = ref(0)
const tipPaused = ref(false)
const tipTimer = setInterval(() => {
  if (!tipPaused.value) tipIndex.value = (tipIndex.value + 1) % tips.length
}, 6000)

const formatDate = (ts) =>
  new Date(ts).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })

// Browser speech-to-text (Chrome, Edge, Safari). No backend needed for the prototype.
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
const speechSupported = !!SpeechRecognition
const listening = ref(false)
let recognition = null

function toggleMic() {
  if (listening.value) {
    recognition.stop()
    return
  }
  const before = form.iAm ? form.iAm.trimEnd() + ' ' : ''
  recognition = new SpeechRecognition()
  recognition.interimResults = true
  recognition.continuous = true
  recognition.onresult = (e) => {
    const spoken = Array.from(e.results, (r) => r[0].transcript).join('')
    form.iAm = before + spoken
  }
  recognition.onend = () => (listening.value = false)
  recognition.start()
  listening.value = true
}

onBeforeUnmount(() => {
  recognition?.stop()
  clearInterval(tipTimer)
})
</script>

<style scoped>
/* ── Logo wrapper ── */
.logo-wrap {
  width: 100%;
  max-width: 480px;
  margin: 0 auto 32px;
}

.box {
  position: relative;
}
textarea {
  width: 100%;
  min-height: 120px;
  padding: 16px 56px 16px 16px;
  resize: vertical;
}
.mic {
  position: absolute;
  right: 10px;
  top: 50%;
  transform: translateY(-50%);
  width: 40px;
  height: 40px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--text);
  display: grid;
  place-items: center;
  cursor: pointer;
}
.mic:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}
.mic.listening {
  background: #e5484d;
  color: white;
  animation: pulse 1.2s ease-in-out infinite;
}
.recent h2 {
  margin: 0 0 8px;
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.recent ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.recent button {
  width: 100%;
  padding: 10px 12px;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: var(--text);
  font: inherit;
  text-align: left;
  display: flex;
  flex-direction: column;
  gap: 2px;
  cursor: pointer;
}
.recent button:hover,
.recent button.active {
  background: var(--surface);
}
.recent .title {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.recent .date {
  font-size: 0.8rem;
  color: var(--muted);
}
.tip {
  margin: 0;
  font-size: 0.85rem;
  color: var(--muted);
  line-height: 1.5;
  cursor: pointer;
}
.tip:hover {
  color: var(--text);
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
.error {
  margin: 0;
  color: #e5484d;
}
@keyframes pulse {
  50% { box-shadow: 0 0 0 8px rgba(229, 72, 77, 0.2); }
}
</style>
