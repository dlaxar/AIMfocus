<template>
  <div class="list-group">
    <section v-for="v in variables" :key="v.name" class="list-var" :class="v.state">
      <h4 class="list-head">
        <span class="var-label">{{ v.label }}</span>
        <StateTag :state="v.state" :note="v.note" />
        <ContradictionBadge :refs="unplacedContradictions(v)" />
      </h4>
      <ul class="fact-list">
        <li v-if="v.state === 'pending'"><span class="skeleton" /></li>
        <li v-for="(it, i) in v.items ?? []" :key="it.fact_id ?? i" :class="{ 'has-cx': it.contradiction_ids?.length }">
          <span class="item-text">
            {{ it.text }}
            <span v-if="STATUS[it.status]" class="status" :class="it.status">{{ STATUS[it.status] }}</span>
          </span>
          <span class="date-cell">{{ formatDate(it.date) }}</span>
          <span class="icon-cell">
            <ContradictionBadge :ids="it.contradiction_ids ?? []" />
            <SourceButton :sources="it.source ? [it.source] : []" />
          </span>
        </li>
      </ul>
    </section>
  </div>
</template>

<script setup>
import ContradictionBadge from './ContradictionBadge.vue'
import SourceButton from './SourceButton.vue'
import StateTag from './StateTag.vue'
import { formatDate } from './format.js'
import { unplacedContradictions } from './layout.js'

// Findings, diagnoses, recommendations: one compact list per variable, newest first.
defineProps({ variables: { type: Array, required: true } })

// Only statuses that change the meaning of the text get a tag
const STATUS = { negative: 'absent', open: 'open', done: 'done', resolved: 'resolved', historical: 'past' }
</script>

<style scoped>
.list-var + .list-var {
  margin-top: 14px;
}
.list-head {
  display: flex;
  align-items: center;
  gap: 2px;
  margin: 0 0 4px;
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--text);
}
.fact-list {
  font-size: 0.9rem;
  line-height: 1.4;
  list-style: none;
  margin: 0;
  padding: 0;
}
.fact-list li {
  display: grid;
  grid-template-columns: 1fr auto auto;
  align-items: baseline;
  gap: 10px;
  padding: 4px 6px 4px 14px;
  position: relative;
  border-bottom: 1px solid var(--row-line);
}
.fact-list li::before {
  content: '';
  position: absolute;
  left: 4px;
  top: 0.9em;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--muted);
}
.fact-list li:last-child {
  border-bottom: none;
}
.status {
  margin-left: 6px;
  padding: 0 5px;
  border-radius: 4px;
  font-size: 0.68rem;
  font-weight: 600;
  background: var(--bg);
  color: var(--muted);
  border: 1px solid var(--border);
}
.status.open {
  background: #fff4e0;
  color: #9a5b00;
  border-color: #f5c56b;
}
.list-var.not_documented .fact-list,
.list-var.pending .item-text {
  color: var(--muted);
}
</style>
