<template>
  <div class="logo-wrap">
    <AIMfocusLogo />
  </div>

  <h1>What's your role,<br> and do you need?</h1>

  <div class="box">
    <textarea v-model="form.iAm" placeholder="I am... and I need..." />
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

onBeforeUnmount(() => recognition?.stop())
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
.error {
  margin: 0;
  color: #e5484d;
}
@keyframes pulse {
  50% { box-shadow: 0 0 0 8px rgba(229, 72, 77, 0.2); }
}
</style>
