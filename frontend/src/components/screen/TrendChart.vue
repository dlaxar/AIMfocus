<template>
  <div class="trend-chart">
    <div class="chart-box">
      <canvas ref="canvas" role="img" :aria-label="ariaLabel" />
    </div>
    <Popover :open="!!active" :anchor="active?.anchor" wide @close="active = null">
      <template v-if="active">
        <ContradictionList v-if="active.point.contradiction_ids?.length" :ids="active.point.contradiction_ids" />
        <SourceExcerpt v-if="active.point.source" :source="active.point.source" />
        <p v-else class="no-source">No source passage for this reading.</p>
      </template>
    </Popover>
  </div>
</template>

<script setup>
import { ref, computed, inject, watch, onMounted, onBeforeUnmount } from 'vue'
import {
  Chart, LineController, LineElement, PointElement, LinearScale, Tooltip, Legend,
} from 'chart.js'
import Popover from './Popover.vue'
import SourceExcerpt from './SourceExcerpt.vue'
import ContradictionList from './ContradictionList.vue'
import { dateValue, formatDate, KIND_LABEL } from './format.js'

Chart.register(LineController, LineElement, PointElement, LinearScale, Tooltip, Legend)

// Line chart of one or more series that share a unit (never two y-axes). Clicking a reading
// opens its source passage; readings a letter contradicts are drawn as red triangles.
const props = defineProps({
  series: { type: Array, required: true }, // [{ name, unit, points, color }]
  unit: { type: String, default: '' },
})

const CX_COLOR = '#ce2c31'
const MUTED = '#80838d'
const GRID = '#eceef1'

const index = inject('contradictionIndex', null)
const canvas = ref(null)
const active = ref(null)
let chart = null

const ariaLabel = computed(() => props.series
  .map((s) => `${s.name}: ${s.points.map((p) => `${p.value} ${s.unit} on ${formatDate(p.date)}`).join(', ')}`)
  .join('. '))

function monthLabel(ms) {
  const d = new Date(ms)
  return formatDate(`${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`)
}

function config() {
  const all = props.series.flatMap((s) => s.points.map((p) => dateValue(p.date)))
  const span = Math.max(...all) - Math.min(...all)
  const pad = Math.max(span * 0.06, 20 * 864e5)

  const datasets = props.series.map((s) => {
    const cx = s.points.map((p) => p.contradiction_ids?.length > 0)
    return {
      label: s.name,
      data: s.points.map((p) => ({ x: dateValue(p.date), y: p.value, point: p })),
      borderColor: s.color,
      backgroundColor: s.color,
      borderWidth: 2,
      tension: 0,
      pointStyle: cx.map((c) => (c ? 'triangle' : 'circle')),
      pointRadius: cx.map((c) => (c ? 6 : 4)),
      pointHoverRadius: cx.map((c) => (c ? 8 : 6)),
      pointHitRadius: 10,
      pointBackgroundColor: cx.map((c) => (c ? CX_COLOR : s.color)),
      pointBorderColor: '#fff',
      pointBorderWidth: 2,
    }
  })

  return {
    type: 'line',
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'nearest', intersect: false, axis: 'x' },
      layout: { padding: { top: 6, right: 8 } },
      scales: {
        x: {
          type: 'linear',
          min: Math.min(...all) - pad,
          max: Math.max(...all) + pad,
          grid: { display: false },
          border: { color: GRID },
          ticks: { color: MUTED, font: { size: 11 }, maxTicksLimit: 5, callback: monthLabel },
        },
        y: {
          grace: '15%',
          grid: { color: GRID },
          border: { display: false },
          ticks: { color: MUTED, font: { size: 11 }, maxTicksLimit: 4 },
        },
      },
      plugins: {
        legend: {
          display: datasets.length > 1,
          position: 'top',
          align: 'start',
          labels: { boxWidth: 10, boxHeight: 2, color: '#1c2024', font: { size: 11 } },
        },
        tooltip: {
          backgroundColor: '#1c2024',
          padding: 8,
          titleFont: { size: 11, weight: '600' },
          bodyFont: { size: 12 },
          footerFont: { size: 10, weight: 'normal' },
          footerColor: '#c3c6cc',
          callbacks: {
            title: (items) => formatDate(items[0].raw.point.date),
            label: (item) => {
              const p = item.raw.point
              const label = p.label && !item.dataset.label.toLowerCase().includes(p.label.toLowerCase()) ? ` (${p.label})` : ''
              return `${item.dataset.label}: ${p.value} ${props.unit}${label}`
            },
            afterBody: (items) => {
              const ids = [...new Set(items.flatMap((i) => i.raw.point.contradiction_ids ?? []))]
              return ids.map((id) => {
                const kind = index?.value.get(id)?.kind
                return `⚠ Contradiction${kind ? `: ${(KIND_LABEL[kind] ?? kind).toLowerCase()}` : ''}`
              })
            },
            footer: () => 'Click for the source',
          },
        },
      },
      onClick: (evt, elements) => {
        const el = elements[0]
        if (!el) return
        const point = chart.data.datasets[el.datasetIndex].data[el.index].point
        const rect = canvas.value.getBoundingClientRect()
        const { x, y } = chart.getDatasetMeta(el.datasetIndex).data[el.index]
        // The popover takes over from the tooltip
        chart.tooltip.setActiveElements([], { x: 0, y: 0 })
        chart.update('none')
        active.value = {
          point,
          anchor: {
            getBoundingClientRect: () => new DOMRect(rect.left + x - 4, rect.top + y - 4, 8, 8),
          },
        }
      },
      onHover: (evt, elements) => {
        evt.native.target.style.cursor = elements.length ? 'pointer' : 'default'
      },
    },
  }
}

function render() {
  chart?.destroy()
  chart = new Chart(canvas.value, config())
}

onMounted(render)
// Screen updates hand in new arrays; redraw only when the readings themselves change
watch(
  () => JSON.stringify(props.series.map((s) => [s.name, s.color, s.points.map((p) => [p.date, p.value, p.contradiction_ids])])),
  render,
)
onBeforeUnmount(() => chart?.destroy())
</script>

<style scoped>
.chart-box {
  position: relative;
  height: 150px;
}
.no-source {
  margin: 0;
  color: var(--muted);
}
</style>
