<template>
  <div class="situation-card">
    <div class="situation-icon" aria-hidden="true">
      <Transition name="icon-swap" mode="out-in">
        <span
          v-if="iconUrl"
          :key="iconUrl"
          class="role-icon"
          :style="{ WebkitMaskImage: `url(${iconUrl})`, maskImage: `url(${iconUrl})` }"
        />
        <svg v-else viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="8" r="4" />
          <path d="M4 20c0-4 3.6-7 8-7s8 3 8 7" />
        </svg>
      </Transition>
    </div>

    <div class="situation-body">
      <Transition name="content-swap" mode="out-in">
        <p :key="displayText" class="situation-text">{{ displayText }}</p>
      </Transition>
    </div>

    <router-link class="edit-link" to="/">Edit</router-link>
  </div>

  <label class="field">
    <span class="label">Patient</span>
    <PatientAutocomplete v-model="selectedPatient" @keyup.enter="next" />
  </label>

  <p v-if="error" class="error" role="alert">{{ error }}</p>

  <Teleport to="body">
    <NextButton :disabled="!selectedPatient || loading" @click="next" />
  </Teleport>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import NextButton from '../components/NextButton.vue'
import PatientAutocomplete from '../components/PatientAutocomplete.vue'
import { form, session, setSessionSummary } from '../store.js'
import { getSessionSummary, setSessionPatient } from '../api.js'

const router = useRouter()
const selectedPatient = ref(null)

watch(selectedPatient, (p) => {
  form.patient = p ? p.id : ''
  form.patientData = p ?? null
})

const loading = ref(false)
const error = ref('')
async function next() {
  if (!selectedPatient.value || loading.value) return
  loading.value = true
  error.value = ''
  try {
    if (session.currentId) await setSessionPatient(session.currentId, selectedPatient.value.id)
    router.push({ path: '/patient/dashboard', state: { patient: selectedPatient.value } })
  } catch (e) {
    error.value = e.message || 'Could not select the patient'
  } finally {
    loading.value = false
  }
}

// Show the summary once available, otherwise fall back to the raw prompt
const displayText = computed(() => {
  const entry = session.recent.find((s) => s.id === session.currentId)
  return entry?.summary ?? form.iAm
})

const iconUrl = computed(() => {
  const entry = session.recent.find((s) => s.id === session.currentId)
  return entry?.icon_url ?? null
})

onMounted(async () => {
  const id = session.currentId
  if (!id) return
  const entry = session.recent.find((s) => s.id === id)
  if (entry?.summary) return
  try {
    const { summary, icon_url } = await getSessionSummary(id)
    if (summary) setSessionSummary(id, summary, icon_url)
  } catch {
    // Summary unavailable – keep showing the raw prompt
  }
})
</script>

<style scoped>
.situation-card {
  display: flex;
  align-items: center;
  gap: 12px;
  background: var(--surface);
  border: 1.5px solid var(--border);
  border-radius: 14px;
  padding: 14px 16px;
  /* Stretch to full width of the .screen content area */
  width: 100%;
  margin-bottom: 20px;
}

.situation-icon {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: var(--bg);
  border: 1.5px solid var(--border);
  display: grid;
  place-items: center;
  color: var(--accent);
}

.situation-body {
  flex: 1;
  min-width: 0;
  /* Reserve enough height so the card doesn't jump during the transition */
  min-height: 1.5em;
}

.situation-text {
  margin: 0;
  font-size: 0.95rem;
  line-height: 1.55;
  color: var(--text);
  word-break: break-word;
}

.edit-link {
  flex-shrink: 0;
  font-size: 0.82rem;
  color: var(--muted);
  text-decoration: none;
  white-space: nowrap;
}
.edit-link:hover {
  text-decoration: underline;
}

.role-icon {
  width: 22px;
  height: 22px;
  /* Use the SVG as a mask so background-color drives the colour,
     picking up --accent automatically regardless of its value. */
  -webkit-mask-size: contain;
  -webkit-mask-repeat: no-repeat;
  -webkit-mask-position: center;
  mask-size: contain;
  mask-repeat: no-repeat;
  mask-position: center;
  background-color: var(--accent);
  /* src is set via inline style on the element */
}

/* ── Icon swap transition ── */
.icon-swap-enter-active {
  transition: opacity 0.3s ease, transform 0.3s ease;
}
.icon-swap-leave-active {
  transition: opacity 0.15s ease;
}
.icon-swap-enter-from {
  opacity: 0;
  transform: scale(0.7);
}
.icon-swap-leave-to {
  opacity: 0;
}

/* ── Summary swap transition ── */
.content-swap-enter-active {
  transition: opacity 0.35s ease, transform 0.35s ease;
}
.content-swap-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.content-swap-enter-from {
  opacity: 0;
  transform: translateY(6px);
}
.content-swap-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

.field {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.label {
  font-size: 1.75rem;
}
.error {
  margin: 12px 0 0;
  color: #e5484d;
}
</style>
