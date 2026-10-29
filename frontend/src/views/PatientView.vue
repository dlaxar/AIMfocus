<template>
  <div class="situation-block">
    <p class="situation-label">{{ displayLabel }}</p>
    <p v-if="situationText !== displayLabel" class="situation-text">{{ situationText }}</p>
    <router-link class="edit-link" to="/">Edit</router-link>
  </div>

  <label class="field">
    <span class="label">Patient</span>
    <PatientAutocomplete v-model="selectedPatient" @keyup.enter="next" />
  </label>

  <Teleport to="body">
    <NextButton :disabled="!selectedPatient" @click="next" />
  </Teleport>
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

// LLM summary as heading; fall back to the raw prompt
const displayLabel = computed(() => {
  const entry = session.recent.find((s) => s.id === session.currentId)
  return entry?.summary ?? form.iAm
})

// Full situation text shown beneath the summary heading
const situationText = computed(() => form.iAm)

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
.situation-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.situation-label {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 600;
  line-height: 1.25;
}
.situation-text {
  margin: 0;
  font-size: 0.9rem;
  color: var(--muted);
  line-height: 1.5;
}
.edit-link {
  font-size: 0.85rem;
  color: var(--accent);
  text-decoration: none;
  align-self: flex-start;
}
.edit-link:hover {
  text-decoration: underline;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.label {
  font-size: 1.75rem;
}
</style>
