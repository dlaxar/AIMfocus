<template>
  <div class="hl-text"><template v-for="(part, i) in parts" :key="i"><mark v-if="part.mark" :ref="(el) => (markEl = el)">{{ part.text }}</mark><template v-else>{{ part.text }}</template></template></div>
</template>

<script setup>
import { computed, watch, nextTick } from 'vue'

// `text` starts at document offset `offset`; [start, end) are document offsets to highlight.
const props = defineProps({
  text: { type: String, required: true },
  offset: { type: Number, default: 0 },
  start: { type: Number, default: null },
  end: { type: Number, default: null },
  scrollToMark: { type: Boolean, default: false },
})

const parts = computed(() => {
  const { text, offset, start, end } = props
  if (start == null || end == null || end <= offset || start >= offset + text.length) {
    return [{ text, mark: false }]
  }
  const a = Math.max(0, start - offset)
  const b = Math.min(text.length, end - offset)
  return [
    { text: text.slice(0, a), mark: false },
    { text: text.slice(a, b), mark: true },
    { text: text.slice(b), mark: false },
  ]
})
let markEl = null
watch(
  () => [props.text, props.start, props.scrollToMark],
  async () => {
    if (!props.scrollToMark) return
    await nextTick()
    markEl?.scrollIntoView({ block: 'center' })
  },
  { immediate: true },
)
</script>

<style scoped>
.hl-text {
  white-space: pre-wrap;
  word-break: break-word;
}
mark {
  background: rgba(60, 133, 132, 0.18);
  color: inherit;
  border-radius: 3px;
  padding: 0 1px;
  box-shadow: 0 0 0 1px rgba(60, 133, 132, 0.45);
}
</style>
