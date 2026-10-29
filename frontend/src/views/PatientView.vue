<template>
  <label class="field">
    <span class="label">Patient</span>
    <PatientAutocomplete v-model="selectedPatient" @keyup.enter="next" />
  </label>

  <NextButton :disabled="!selectedPatient" @click="next" />
</template>

<script setup>
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import NextButton from '../components/NextButton.vue'
import PatientAutocomplete from '../components/PatientAutocomplete.vue'
import { form } from '../store.js'

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
</script>

<style scoped>
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
