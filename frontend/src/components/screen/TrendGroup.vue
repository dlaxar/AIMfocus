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
        <div v-if="charts(v).length" class="charts" :class="{ multi: charts(v).length > 1 }">
          <figure v-for="c in charts(v)" :key="c.unit" class="chart-figure">
            <figcaption>{{ c.series.map((s) => s.name).join(' / ') }}<template v-if="c.unit"> ({{ c.unit }})</template></figcaption>
            <TrendChart :series="c.series" :unit="c.unit" />
          </figure>
        </div>
        <div v-for="s in v.series" :key="s.name" class="series-points">
          <span class="series-name">
            <span class="swatch" :style="{ background: colorOf(v, s) }" aria-hidden="true" />{{ seriesName(v, s) }}
          </span>
          <span
            v-for="(p, i) in visiblePoints(v, s)"
            :key="i"
            class="point-chip"
            :class="{ 'has-cx': p.contradiction_ids?.length }"
          >
            <span class="point-value">{{ formatNumber(p.value) }} {{ s.unit }}</span>
            <span class="date-cell">{{ formatDate(p.date) }}<template v-if="showPointLabel(s, p)"> · {{ p.label }}</template></span>
            <ContradictionBadge :ids="p.contradiction_ids ?? []" />
            <SourceButton :sources="p.source ? [p.source] : []" />
          </span>
          <button
            v-if="olderPoints(s).size"
            type="button"
            class="history-toggle"
            :aria-expanded="!!expanded[key(v, s)]"
            @click="expanded[key(v, s)] = !expanded[key(v, s)]"
          >
            {{ expanded[key(v, s)] ? 'Hide earlier' : `${olderPoints(s).size} earlier` }}
          </button>
        </div>
      </template>
    </section>
  </div>
</template>

<script setup>
import { reactive } from 'vue'
import ContradictionBadge from './ContradictionBadge.vue'
import SourceButton from './SourceButton.vue'
import StateTag from './StateTag.vue'
import TrendChart from './TrendChart.vue'
import { formatDate } from './format.js'
import { unplacedContradictions } from './layout.js'

// Measured quantities over time: headline, a chart per unit, then the latest readings with their
// source; earlier readings open on demand.
defineProps({ variables: { type: Array, required: true } })

const LATEST_CHIPS = 2
const expanded = reactive({})

function key(v, s) {
  return `${v.name}/${s.name}`
}

// All but the latest few readings; a contradicted reading is never tucked away
function olderPoints(s) {
  const byDate = [...s.points].sort((a, b) => String(a.date ?? '').localeCompare(String(b.date ?? '')))
  return new Set(byDate.slice(0, -LATEST_CHIPS).filter((p) => !p.contradiction_ids?.length))
}

function visiblePoints(v, s) {
  if (expanded[key(v, s)]) return s.points
  const older = olderPoints(s)
  return s.points.filter((p) => !older.has(p))
}

// Categorical slots in fixed order (validated palette; see the dataviz skill's reference palette)
const SERIES_COLORS = ['#2a78d6', '#eb6834', '#1baf7a']

function colorOf(v, s) {
  return SERIES_COLORS[v.series.indexOf(s) % SERIES_COLORS.length]
}

// One chart per unit (weight and BMI never share an axis); a single reading is no trend
function charts(v) {
  const byUnit = new Map()
  for (const s of v.series ?? []) {
    if (!s.points.length) continue
    if (!byUnit.has(s.unit)) byUnit.set(s.unit, [])
    byUnit.get(s.unit).push({ ...s, name: seriesName(v, s), color: colorOf(v, s) })
  }
  return [...byUnit.entries()]
    .filter(([, series]) => series.some((s) => new Set(s.points.map((p) => p.date)).size > 1))
    .map(([unit, series]) => ({ unit, series }))
}

// "Blood pressure – Systolic" under "Blood pressure" reads as just "Systolic"
function seriesName(v, s) {
  const prefix = `${v.label} – `
  return s.name.startsWith(prefix) ? s.name.slice(prefix.length) : s.name
}

// "24h mean" tells readings apart; "Weight" under "Body weight" says nothing new
function showPointLabel(s, p) {
  return p.label && !s.name.toLowerCase().includes(p.label.toLowerCase())
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
.charts {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
  margin-bottom: 6px;
}
@media (min-width: 640px) {
  .charts.multi {
    grid-template-columns: 1fr 1fr;
  }
}
.chart-figure {
  margin: 0;
  min-width: 0;
}
.chart-figure figcaption {
  font-size: 0.74rem;
  color: var(--muted);
  margin-bottom: 2px;
}
.swatch {
  display: inline-block;
  width: 10px;
  height: 2px;
  margin-right: 5px;
  vertical-align: middle;
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
