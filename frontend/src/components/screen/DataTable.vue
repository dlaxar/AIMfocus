<template>
  <table class="fact-table data-table">
    <thead>
      <tr>
        <th v-for="c in columns" :key="c" scope="col">{{ COLUMN_LABEL[c] ?? c.replace(/_/g, ' ') }}</th>
        <th class="col-icons" scope="col"><span class="sr-only">Contradictions and sources</span></th>
      </tr>
    </thead>
    <tbody v-for="v in variables" :key="v.name" :class="['var', v.state]">
      <tr class="group-row">
        <th :colspan="columns.length + 1" scope="rowgroup">
          <span class="var-label">{{ v.label }}</span>
          <StateTag :state="v.state" :note="v.note" />
          <ContradictionBadge :refs="unplacedContradictions(v)" />
        </th>
      </tr>
      <tr v-if="v.state === 'pending'">
        <td v-for="c in columns" :key="c"><span class="skeleton" /></td>
        <td />
      </tr>
      <tr v-for="(row, i) in v.table?.rows ?? []" :key="row.fact_id ?? i" :class="{ 'has-cx': row.contradiction_ids?.length }">
        <td v-for="c in columns" :key="c" :class="`cell-${c}`">{{ cellText(row.cells[c], c) }}</td>
        <td class="icon-cell">
          <ContradictionBadge :ids="row.contradiction_ids ?? []" />
          <SourceButton :sources="row.source ? [row.source] : []" />
        </td>
      </tr>
    </tbody>
  </table>
</template>

<script setup>
import { computed } from 'vue'
import ContradictionBadge from './ContradictionBadge.vue'
import SourceButton from './SourceButton.vue'
import StateTag from './StateTag.vue'
import { formatDate } from './format.js'
import { tableColumns, unplacedContradictions } from './layout.js'

// The tier's table variables merged into one table over the union of their columns,
// one row group per variable.
const props = defineProps({ variables: { type: Array, required: true } })

const COLUMN_LABEL = {
  item: 'Item', strength: 'Strength', schedule: 'Schedule', route: 'Route', class: 'Class',
  status: 'Status', since: 'Since', last_change: 'Last change', value: 'Value', date: 'Date',
}

const columns = computed(() => tableColumns(props.variables))

function cellText(text, column) {
  if (!text) return ''
  if (column === 'date' || column === 'since') return formatDate(text)
  if (column === 'class' || column === 'status') return text.replace(/_/g, ' ')
  return text
}
</script>
