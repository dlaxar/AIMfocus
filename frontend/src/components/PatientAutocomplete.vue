<template>
  <div class="autocomplete" ref="rootEl">
    <input
      ref="inputEl"
      type="text"
      class="autocomplete-input"
      :placeholder="placeholder"
      :value="inputText"
      autocomplete="off"
      role="combobox"
      aria-autocomplete="list"
      :aria-expanded="open"
      aria-haspopup="listbox"
      :aria-activedescendant="activeId"
      @input="onInput"
      @keydown="onKeydown"
      @focus="onFocus"
      @blur="onBlur"
    />
    <span v-if="loading" class="spinner" aria-hidden="true" />

    <ul
      v-if="open && items.length"
      role="listbox"
      class="dropdown"
    >
      <li
        v-for="(item, idx) in items"
        :key="item.id"
        :id="`pac-opt-${item.id}`"
        role="option"
        :aria-selected="idx === activeIdx"
        :class="{ active: idx === activeIdx }"
        @mousedown.prevent="select(item)"
        @mouseover="activeIdx = idx"
      >
        <span class="patient-name">{{ item.name }}</span>
        <span class="patient-meta">{{ item.id }}<template v-if="item.dateOfBirth"> · born {{ formatDate(item.dateOfBirth) }}</template></span>
      </li>
    </ul>

    <ul v-else-if="open && !loading && query.length >= 1" class="dropdown empty">
      <li class="no-results">No patients found</li>
    </ul>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { searchPatients } from '../api.js'
import { formatDate } from './screen/format.js'

const props = defineProps({
  modelValue: { type: Object, default: null }, // { id, name, dateOfBirth, … } or null
  placeholder: { type: String, default: 'Search by name, ID or date of birth…' },
})

const emit = defineEmits(['update:modelValue'])

// ── state ─────────────────────────────────────────────────────────────────
const inputText = ref(props.modelValue?.name ?? '')
const query     = ref('')
const items     = ref([])
const loading   = ref(false)
const open      = ref(false)
const activeIdx = ref(-1)
const rootEl    = ref(null)
const inputEl   = ref(null)

let debounceTimer = null

const activeId = computed(() =>
  activeIdx.value >= 0 && items.value[activeIdx.value]
    ? `pac-opt-${items.value[activeIdx.value].id}`
    : undefined
)

// ── watchers ──────────────────────────────────────────────────────────────
watch(() => props.modelValue, (val) => {
  inputText.value = val?.name ?? ''
})

// ── methods ───────────────────────────────────────────────────────────────
function onInput(e) {
  const val = e.target.value
  inputText.value = val
  query.value = val

  // Clear selection when user edits the text
  if (props.modelValue !== null) emit('update:modelValue', null)

  clearTimeout(debounceTimer)
  if (val.trim().length === 0) {
    items.value = []
    open.value = false
    return
  }
  debounceTimer = setTimeout(() => fetchPatients(val.trim()), 250)
}

async function fetchPatients(q) {
  loading.value = true
  open.value = true
  activeIdx.value = -1
  try {
    items.value = await searchPatients(q)
  } catch {
    items.value = []
  } finally {
    loading.value = false
  }
}

function select(item) {
  inputText.value = item.name
  query.value = ''
  items.value = []
  open.value = false
  activeIdx.value = -1
  emit('update:modelValue', item)
}

function onKeydown(e) {
  if (!open.value) return
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    activeIdx.value = Math.min(activeIdx.value + 1, items.value.length - 1)
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    activeIdx.value = Math.max(activeIdx.value - 1, 0)
  } else if (e.key === 'Enter') {
    if (activeIdx.value >= 0 && items.value[activeIdx.value]) {
      e.preventDefault()
      select(items.value[activeIdx.value])
    }
  } else if (e.key === 'Escape') {
    open.value = false
  }
}

function onFocus() {
  if (items.value.length) open.value = true
}

function onBlur() {
  // Small delay so mousedown on a list item fires first
  setTimeout(() => { open.value = false }, 150)
}
</script>

<style scoped>
.autocomplete {
  position: relative;
  width: 100%;
}

.autocomplete-input {
  width: 100%;
  padding: 14px 40px 14px 16px;
  box-sizing: border-box;
}

.spinner {
  position: absolute;
  right: 12px;
  top: 50%;
  transform: translateY(-50%);
  width: 18px;
  height: 18px;
  border: 2px solid var(--border, #ccc);
  border-top-color: var(--text, #333);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
  pointer-events: none;
}

@keyframes spin {
  to { transform: translateY(-50%) rotate(360deg); }
}

.dropdown {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  background: var(--bg, #fff);
  border: 1px solid var(--border, #ccc);
  border-radius: 8px;
  list-style: none;
  margin: 0;
  padding: 4px 0;
  max-height: 260px;
  overflow-y: auto;
  z-index: 100;
  box-shadow: 0 4px 16px rgba(0,0,0,0.12);
}

.dropdown li {
  padding: 10px 16px;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8px;
}

.dropdown li.active,
.dropdown li:hover {
  background: var(--surface, #f5f5f5);
}

.patient-name {
  font-weight: 500;
}

.patient-meta {
  font-size: 0.8rem;
  color: var(--muted, #888);
  white-space: nowrap;
}

.no-results {
  color: var(--muted, #888);
  font-size: 0.9rem;
  cursor: default;
}

.dropdown.empty li:hover {
  background: transparent;
}
</style>
