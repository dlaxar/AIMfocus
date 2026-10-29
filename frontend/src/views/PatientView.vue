<template>
  <p class="role-badge" role="button" tabindex="0" @click="router.push('/')" @keyup.enter="router.push('/')">{{ displayLabel }}</p>

  <label class="field">
    <span class="label">Patient</span>
    <PatientAutocomplete v-model="selectedPatient" @keyup.enter="next" />
  </label>

  <NextButton :disabled="!selectedPatient" @click="next" />
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import NextButton from '../components/NextButton.vue'
import PatientAutocomplete from '../components/PatientAutocomplete.vue'
import { form, session, setSessionSummary } from '../store.js'
import { getSessionSummary } from '../api.js'

const router = useRouter()
const selectedPatient = ref(null)

// Keep store in sync so later views can read form.patient
watch(selectedPatient, (p) => {
  form.patient = p ? p.id : ''
})

function next() {
  if (!selectedPatient.value) return
  router.push({ path: '/patient/dashboard', state: { patient: selectedPatient.value } })
}

// Display the LLM summary if available, otherwise fall back to the raw prompt
const displayLabel = computed(() => {
  const entry = session.recent.find((s) => s.id === session.currentId)
  return entry?.summary ?? form.iAm
})

onMounted(async () => {
  const id = session.currentId
  if (!id) return
  const entry = session.recent.find((s) => s.id === id)
  // Already fetched a summary for this session – nothing to do
  if (entry?.summary) return
  try {
    const summary = await getSessionSummary(id)
    if (summary) setSessionSummary(id, summary)
  } catch {
    // Summary unavailable – keep showing the raw prompt (no error displayed)
  }
})
</script>

<style scoped>
.role-badge {
  margin: 0;
  font-size: 0.85rem;
  color: var(--muted);
  background: var(--surface);
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 20px;
  padding: 4px 14px;
  align-self: flex-start;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
  cursor: pointer;
}
.role-badge:hover {
  background: var(--border, #e5e7eb);
}
.field {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.label {
  font-size: 1.75rem;
}
</style>
