<template>
  <div class="cx-list">
    <div v-for="cx in items" :key="cx.contradiction_id" class="cx">
      <div class="cx-kind">Contradiction · {{ KIND_LABEL[cx.kind] ?? cx.kind }}</div>
      <p>{{ cx.summary_en }}</p>
    </div>
  </div>
</template>

<script setup>
import { computed, inject } from 'vue'
import { KIND_LABEL } from './format.js'

// Plain summaries of contradictions, for places that already show the passages themselves.
const props = defineProps({ ids: { type: Array, required: true } })
const index = inject('contradictionIndex', null)
const items = computed(() => props.ids.map((id) => index?.value.get(id) ?? { contradiction_id: id, summary_en: id }))
</script>

<style scoped>
.cx-list {
  margin-bottom: 10px;
  padding: 8px 10px;
  border-radius: 6px;
  background: #fff6f5;
  border: 1px solid #f2b8b5;
}
.cx + .cx {
  margin-top: 8px;
}
.cx-kind {
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #ce2c31;
}
p {
  margin: 2px 0 0;
  font-size: 0.82rem;
}
</style>
