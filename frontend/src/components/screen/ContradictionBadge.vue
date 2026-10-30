<template>
  <span v-if="items.length" class="cx-wrap">
    <button
      ref="btn"
      type="button"
      class="icon-btn cx-btn"
      :class="{ active: pinned }"
      :aria-label="`${items.length} contradiction(s) – show details`"
      @click.stop="toggle"
      @mouseenter="hoverIn"
      @mouseleave="hoverOut"
      @focus="hoverIn"
      @blur="hoverOut"
    >
      <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
        <path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" fill="currentColor"/>
        <path d="M12 9v4M12 17h.01" stroke="#fff" stroke-width="2.2" stroke-linecap="round"/>
      </svg>
      <span v-if="items.length > 1 && !hideCount" class="count">{{ items.length }}</span>
    </button>
    <Popover
      :open="pinned || hovering"
      :anchor="btn"
      :wide="pinned"
      :transient="!pinned"
      @close="close"
      @enter="hoverIn"
      @leave="hoverOut"
    >
      <section v-for="cx in items" :key="cx.contradiction_id" class="cx">
        <div class="cx-kind">
          <svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" fill="currentColor"/></svg>
          Contradiction · {{ KIND_LABEL[cx.kind] ?? cx.kind }}
        </div>
        <p class="cx-summary">{{ cx.summary_en }}</p>
        <template v-if="pinned">
          <p v-if="cx.judge?.rationale" class="cx-judge">
            {{ cx.judge.rationale }}
            <span v-if="cx.judge.confidence != null"> · confidence {{ Math.round(cx.judge.confidence * 100) }}%</span>
          </p>
          <div v-if="cx.passage_ids?.length" class="cx-passages">
            <div class="cx-passages-label">Conflicting passages</div>
            <SourceExcerpt v-for="pid in cx.passage_ids" :key="pid" :source="{ passage_id: pid }" />
          </div>
        </template>
      </section>
      <p v-if="!pinned" class="cx-hint">Click the icon to see the conflicting passages.</p>
    </Popover>
  </span>
</template>

<script setup>
import { ref, computed, inject } from 'vue'
import Popover from './Popover.vue'
import SourceExcerpt from './SourceExcerpt.vue'
import { KIND_LABEL } from './format.js'

// Warning icon for one or more contradictions. Hover: summary. Click: details and passages.
const props = defineProps({
  ids: { type: Array, default: () => [] },
  // ContradictionRef objects from a variable, used when the screen-level list lacks an ID
  refs: { type: Array, default: () => [] },
  // Icon only, where the count is already spelled out next to it
  hideCount: { type: Boolean, default: false },
})

const index = inject('contradictionIndex', null)

const items = computed(() => {
  const byId = new Map(props.refs.map((r) => [r.contradiction_id, r]))
  const ids = [...new Set([...props.ids, ...byId.keys()])]
  return ids.map((id) => index?.value.get(id) ?? byId.get(id) ?? { contradiction_id: id, summary_en: id })
})

const btn = ref(null)
const pinned = ref(false)
const hovering = ref(false)
let hoverTimer = null

function hoverIn() {
  clearTimeout(hoverTimer)
  hovering.value = true
}
function hoverOut() {
  clearTimeout(hoverTimer)
  hoverTimer = setTimeout(() => (hovering.value = false), 120)
}
function toggle() {
  pinned.value = !pinned.value
  if (!pinned.value) hovering.value = false
}
function close() {
  pinned.value = false
  hovering.value = false
}
</script>

<style scoped>
.cx-wrap {
  display: inline-flex;
}
.icon-btn.cx-btn,
.icon-btn.cx-btn:hover,
.icon-btn.cx-btn.active {
  color: #ce2c31;
}
.count {
  font-size: 0.65rem;
  font-weight: 600;
  margin-left: 1px;
}
.cx + .cx {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}
.cx-kind {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 0.72rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #ce2c31;
}
.cx-summary {
  margin: 4px 0 0;
}
.cx-judge {
  margin: 6px 0 0;
  font-size: 0.78rem;
  color: var(--muted);
}
.cx-passages {
  margin-top: 10px;
}
.cx-passages-label {
  font-size: 0.72rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
  margin-bottom: 6px;
}
.cx-hint {
  margin: 8px 0 0;
  font-size: 0.75rem;
  color: var(--muted);
}
</style>
