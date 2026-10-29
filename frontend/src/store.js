import { reactive } from 'vue'

// Answers collected across the screens. Swap for a POST to /api/... once the backend exists.
export const form = reactive({
  iAm: '',
  patient: '',
})
