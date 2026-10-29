<template>
  <label class="field">
    <span class="label">Patient</span>
    <input v-model="form.patient" type="text" placeholder="Name or ID" @keyup.enter="next" />
  </label>

  <NextButton :disabled="!form.patient.trim()" @click="next" />
</template>

<script setup>
import NextButton from '../components/NextButton.vue'
import { form } from '../store.js'

function next() {
  if (!form.patient.trim()) return
  // TODO: send to backend, e.g. fetch('/api/intake', { method: 'POST', body: JSON.stringify(form) })
  console.log('Collected:', { ...form })
}
</script>

<style scoped>
/* The sketch puts the field in the middle of the screen */
.field {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.label {
  font-size: 1.75rem;
}
input {
  padding: 14px 16px;
}
</style>
