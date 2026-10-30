<template>
  <table class="fact-table value-table">
    <colgroup>
      <col class="col-label" />
      <col />
      <col class="col-date" />
      <col class="col-icons" />
    </colgroup>
    <tbody v-for="v in variables" :key="v.name" :class="['var', v.state]">
      <tr v-for="(e, i) in visibleEntries(v)" :key="i" :class="{ 'has-cx': e.contradiction_ids?.length, older: e.older }">
        <th v-if="i === 0" scope="row" :rowspan="visibleEntries(v).length" class="label-cell">
          <span class="var-label">{{ v.label }}</span>
          <ContradictionBadge :refs="unplacedContradictions(v)" />
          <button
            v-if="v.history?.length"
            type="button"
            class="history-toggle"
            :aria-expanded="!!expanded[v.name]"
            @click="expanded[v.name] = !expanded[v.name]"
          >
            {{ expanded[v.name] ? 'Hide earlier' : `${v.history.length} earlier` }}
          </button>
        </th>
        <td class="value-cell">
          <span v-if="v.state === 'pending'" class="skeleton" />
          <span v-else-if="e.missing" class="missing" :title="v.note || ''">{{ e.text }}</span>
          <template v-else>
            {{ e.text }}<StateTag v-if="i === 0" :state="v.state" :note="v.note" />
          </template>
        </td>
        <td class="date-cell">{{ formatDate(e.date) }}</td>
        <td class="icon-cell">
          <ContradictionBadge :ids="e.contradiction_ids ?? []" />
          <SourceButton v-if="!e.missing && v.state !== 'pending'" :sources="e.sources" />
        </td>
      </tr>
    </tbody>
  </table>
</template>

<script setup>
import { reactive } from 'vue'
import ContradictionBadge from './ContradictionBadge.vue'
import SourceButton from './SourceButton.vue'
import StateTag from './StateTag.vue'
import { formatDate } from './format.js'
import { unplacedContradictions } from './layout.js'

// All single-value variables of a tier in one table: label | value | date | contradiction + source.
// A variable with several current values (one per concept) gets one row each; older values
// open below on demand.
defineProps({ variables: { type: Array, required: true } })

const expanded = reactive({})

function asRow(e, older = false) {
  return { ...e, older, sources: e.source ? [e.source] : [] }
}

function visibleEntries(v) {
  if (v.state === 'pending') return [{ text: '' }]
  if (v.state === 'not_documented' || v.state === 'not_available') {
    return [{ text: v.headline?.text || (v.state === 'not_available' ? 'No images in the documents' : 'Not documented'), missing: true }]
  }
  const latest = v.latest?.length
    ? v.latest.map((e) => asRow(e))
    : [{ text: v.headline?.text ?? v.value ?? '', date: v.headline?.date, sources: v.sources, contradiction_ids: v.contradiction_ids }]
  const history = expanded[v.name] ? (v.history ?? []).map((e) => asRow(e, true)) : []
  return [...latest, ...history]
}
</script>
