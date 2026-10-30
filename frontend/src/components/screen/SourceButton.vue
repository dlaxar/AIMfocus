<template>
  <span class="source-btn-wrap">
    <button
      ref="btn"
      type="button"
      class="icon-btn source-btn"
      :class="{ active: open }"
      :disabled="!list.length"
      :aria-label="list.length ? `Show ${list.length} source(s)` : 'No source'"
      :title="list.length ? `${list.length} source${list.length > 1 ? 's' : ''}` : 'No source'"
      @click.stop="open = !open"
    >
      <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
        <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z M14 3v5h5 M9 13h6 M9 17h4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <span v-if="list.length > 1" class="count">{{ list.length }}</span>
    </button>
    <Popover :open="open" :anchor="btn" wide @close="open = false">
      <SourceExcerpt v-for="s in list" :key="s.passage_id ?? s.start" :source="s" />
    </Popover>
  </span>
</template>

<script setup>
import { ref, computed } from 'vue'
import Popover from './Popover.vue'
import SourceExcerpt from './SourceExcerpt.vue'
import { uniqueSources } from './format.js'

// Icon for a fact's sources; the passages are only loaded and shown when it is clicked.
const props = defineProps({ sources: { type: Array, default: () => [] } })

const btn = ref(null)
const open = ref(false)
const list = computed(() => uniqueSources(props.sources))
</script>

<style scoped>
.source-btn-wrap {
  display: inline-flex;
}
.count {
  font-size: 0.65rem;
  font-weight: 600;
  margin-left: 1px;
}
</style>
