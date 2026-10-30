<template>
  <span v-if="tag" class="state-tag" :class="state" :title="note || tag.title">{{ tag.text }}</span>
</template>

<script setup>
import { computed } from 'vue'

// Small hint next to a value: found in free text, failed verification, etc.
const props = defineProps({
  state: { type: String, default: 'documented' },
  note: { type: String, default: null },
})

const TAGS = {
  derived: { text: 'from text', title: 'Found by the text search in a letter; quote checked' },
  check: { text: 'check', title: 'An answer existed but failed verification' },
}
const tag = computed(() => TAGS[props.state])
</script>

<style scoped>
.state-tag {
  display: inline-block;
  margin-left: 6px;
  padding: 0 5px;
  border-radius: 4px;
  font-size: 0.68rem;
  font-weight: 600;
  line-height: 1.5;
  vertical-align: 1px;
  white-space: nowrap;
  background: var(--bg);
  color: var(--muted);
  border: 1px solid var(--border);
  cursor: help;
}
.state-tag.check {
  background: #fff4e0;
  color: #9a5b00;
  border-color: #f5c56b;
}
</style>
