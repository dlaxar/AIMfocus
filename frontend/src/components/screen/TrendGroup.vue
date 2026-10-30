<template>
  <div class="trend-group">
    <section v-for="v in variables" :key="v.name" class="trend-var" :class="v.state">
      <h4 class="trend-head">
        <span class="var-label">{{ v.label }}</span>
        <StateTag :state="v.state" :note="v.note" />
        <ContradictionBadge :refs="unplacedContradictions(v)" />
      </h4>
      <div v-if="v.state === 'pending'" class="skeleton chart-skeleton" />
      <template v-else>
        <p v-if="v.headline" class="trend-headline">
          <strong>{{ v.headline.text }}</strong>
          <span v-if="v.headline.date" class="date-cell">{{ formatDate(v.headline.date) }}</span>
          <span v-if="v.headline.change" class="change">{{ v.headline.change }}</span>
        </p>
        <div v-for="s in v.series" :key="s.name" class="series-points">
          <span class="series-name">{{ seriesName(v, s) }}</span>
          <span
            v-for="(p, i) in s.points"
            :key="i"
            class="point-chip"
            :class="{ 'has-cx': p.contradiction_ids?.length }"
          >
            <span class="point-value">{{ formatNumber(p.value) }} {{ s.unit }}</span>
            <span class="date-cell">{{ formatDate(p.date) }}<template v-if="p.label && p.label !== s.name && !s.name.includes(p.label)"> · {{ p.label }}</template></span>
            <ContradictionBadge :ids="p.contradiction_ids ?? []" />
            <SourceButton :sources="p.source ? [p.source] : []" />
          </span>
        </div>
      </template>
    </section>
  </div>
</template>

<script setup>
import ContradictionBadge from './ContradictionBadge.vue'
import SourceButton from './SourceButton.vue'
import StateTag from './StateTag.vue'
import { formatDate } from './format.js'
import { unplacedContradictions } from './layout.js'

// Measured quantities over time: headline, then every reading with its source.
defineProps({ variables: { type: Array, required: true } })

// "Blood pressure – Systolic" under "Blood pressure" reads as just "Systolic"
function seriesName(v, s) {
  const prefix = `${v.label} – `
  return s.name.startsWith(prefix) ? s.name.slice(prefix.length) : s.name
}

function formatNumber(n) {
  return Number.isInteger(n) ? String(n) : n.toFixed(1).replace(/\.0$/, '')
}
</script>

<style scoped>
.trend-group {
  display: grid;
  grid-template-columns: 1fr;
  gap: 18px;
}
.trend-head {
  display: flex;
  align-items: center;
  gap: 2px;
  margin: 0;
  font-size: 0.8rem;
  font-weight: 600;
}
.trend-headline {
  margin: 2px 0 6px;
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 10px;
}
.change {
  font-size: 0.8rem;
  color: var(--muted);
}
.chart-skeleton {
  display: block;
  height: 120px;
  width: 100%;
  margin-top: 6px;
}
.series-points {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px 6px;
  font-size: 0.8rem;
  margin-top: 4px;
}
.series-name {
  color: var(--muted);
  min-width: 70px;
}
.point-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px 2px 1px 8px;
  border-radius: 6px;
  border: 1px solid var(--row-line);
  background: var(--surface);
}
.point-chip.has-cx {
  border-color: #f2b8b5;
  background: #fff6f5;
}
.point-value {
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
</style>
