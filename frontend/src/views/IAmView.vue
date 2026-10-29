<template>
  <h1>I am</h1>

  <div class="box">
    <textarea v-model="form.iAm" placeholder="Type or tap the mic to speak…" />
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

  <NextButton :disabled="!form.iAm.trim()" @click="router.push('/patient')" />
</template>

<script setup>
import { ref, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import NextButton from '../components/NextButton.vue'
import { form } from '../store.js'

const router = useRouter()

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
.box {
  position: relative;
}
textarea {
  width: 100%;
  min-height: 240px;
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
@keyframes pulse {
  50% { box-shadow: 0 0 0 8px rgba(229, 72, 77, 0.2); }
}
</style>
