<template>
  <div class="image-viewer">
    <!-- Preview: axial, coronal and sagittal middle slice side by side; a click opens the slices -->
    <button v-if="!scrolling" type="button" class="preview" :title="`Scroll through the slices`" @click="open">
      <img :src="image.preview_url" :alt="`${title} – axial, coronal and sagittal middle slice`" loading="lazy" />
      <span class="preview-hint">Click to scroll through slices</span>
    </button>

    <div v-else class="slices">
      <div class="toolbar">
        <div class="axes" role="group" aria-label="Plane">
          <button
            v-for="a in AXES"
            :key="a"
            type="button"
            class="axis-btn"
            :class="{ active: axis === a }"
            :aria-pressed="axis === a"
            @click="setAxis(a)"
          >
            {{ AXIS_LABEL[a] }}
          </button>
        </div>
        <span class="slice-count">{{ count ? `${index + 1} / ${count}` : '…' }}</span>
        <a class="toolbar-link" :href="image.dicom_url" download>DICOM</a>
        <button type="button" class="toolbar-link" @click="scrolling = false">Close</button>
      </div>

      <div
        ref="frame"
        class="frame"
        tabindex="0"
        :aria-label="`${title}, ${AXIS_LABEL[axis]} slice ${index + 1} of ${count}. Arrow keys or mouse wheel to scroll.`"
        @wheel.prevent="onWheel"
        @keydown="onKey"
      >
        <img v-if="src" :src="src" :alt="`${AXIS_LABEL[axis]} slice ${index + 1}`" draggable="false" />
        <p v-if="error" class="frame-error">{{ error }}</p>
        <span v-else-if="!src" class="skeleton frame-skeleton" />
      </div>

      <input
        v-if="count > 1"
        class="slider"
        type="range"
        min="0"
        :max="count - 1"
        :value="index"
        :aria-label="`${AXIS_LABEL[axis]} slice`"
        @input="show(axis, Number($event.target.value))"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, onBeforeUnmount } from 'vue'

// One DICOM image (an ImageRef, screen-api.md section 4): the preview, and on click its slices
// (core: /slice.png?axis=…&index=…), scrolled with the wheel, arrow keys or the slider.
const props = defineProps({
  image: { type: Object, required: true },
  title: { type: String, default: 'Image' },
})

const AXES = ['axial', 'coronal', 'sagittal']
const AXIS_LABEL = { axial: 'Axial', coronal: 'Coronal', sagittal: 'Sagittal' }

const scrolling = ref(false)
const axis = ref('axial')
const index = ref(0)          // the slice asked for; src catches up once it has loaded
const count = ref(0)
const src = ref(null)
const error = ref('')
const frame = ref(null)

const counts = {}             // axis → slices on that axis (X-Slice-Count)
const cache = new Map()       // "axis:index" → object URL of the PNG

async function fetchSlice(a, i) {
  const key = `${a}:${i ?? 'mid'}`
  if (cache.has(key)) return { url: cache.get(key), index: i ?? Math.floor(counts[a] / 2) }
  const query = new URLSearchParams({ axis: a })
  if (i != null) query.set('index', i)
  const res = await fetch(`${props.image.slice_url}?${query}`)
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || `Slice could not be loaded (${res.status})`)
  }
  counts[a] = Number(res.headers.get('X-Slice-Count')) || 1
  const url = URL.createObjectURL(await res.blob())
  // No index = the middle slice (n // 2 on the core)
  const at = i ?? Math.floor(counts[a] / 2)
  cache.set(`${a}:${at}`, url)
  if (i == null) cache.set(key, url)
  return { url, index: at }
}

// Fast scrolling asks for many slices: load one at a time and skip to the newest request
let wanted = null
let busy = false

async function show(a, i) {
  wanted = { a, i }
  if (i != null) index.value = i
  if (busy) return
  busy = true
  try {
    while (wanted) {
      const w = wanted
      wanted = null
      try {
        const r = await fetchSlice(w.a, w.i)
        if (w.a !== axis.value) continue
        count.value = counts[w.a]
        src.value = r.url
        error.value = ''
        if (w.i == null) index.value = r.index
      } catch (e) {
        error.value = e.message
      }
    }
  } finally {
    busy = false
  }
}

function step(delta) {
  if (!count.value) return
  const next = Math.max(0, Math.min(count.value - 1, index.value + delta))
  if (next !== index.value) show(axis.value, next)
}

function setAxis(a) {
  if (a === axis.value) return
  axis.value = a
  count.value = counts[a] ?? 0
  src.value = null
  show(a, null)
}

async function open() {
  scrolling.value = true
  if (!src.value) show(axis.value, null)
  await nextTick()
  frame.value?.focus()
}

// Trackpads send many small deltas, a mouse wheel few large ones: one slice per ~30px
let wheelRest = 0
function onWheel(e) {
  wheelRest += e.deltaY
  const steps = Math.trunc(wheelRest / 30)
  if (steps) {
    wheelRest -= steps * 30
    step(steps)
  }
}

const KEY_STEP = { ArrowUp: -1, ArrowLeft: -1, ArrowDown: 1, ArrowRight: 1, PageUp: -10, PageDown: 10 }
function onKey(e) {
  if (e.key in KEY_STEP) {
    e.preventDefault()
    step(KEY_STEP[e.key])
  } else if (e.key === 'Home' || e.key === 'End') {
    e.preventDefault()
    show(axis.value, e.key === 'Home' ? 0 : count.value - 1)
  } else if (e.key === 'Escape') {
    scrolling.value = false
  }
}

onBeforeUnmount(() => {
  new Set(cache.values()).forEach((url) => URL.revokeObjectURL(url))
})
</script>

<style scoped>
.image-viewer {
  max-width: 100%;
}

.preview {
  position: relative;
  display: block;
  max-width: 100%;
  padding: 0;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  background: #000;
  cursor: zoom-in;
}
.preview img {
  display: block;
  max-width: 100%;
  max-height: 240px;
}
.preview-hint {
  position: absolute;
  right: 6px;
  bottom: 6px;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.72rem;
  color: #fff;
  background: rgba(0, 0, 0, 0.6);
  opacity: 0;
  transition: opacity 0.15s;
}
.preview:hover .preview-hint,
.preview:focus-visible .preview-hint {
  opacity: 1;
}

.slices {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-width: 420px;
}

.toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 0.78rem;
}
.axes {
  display: inline-flex;
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
}
.axis-btn {
  padding: 2px 10px;
  border: none;
  background: var(--surface);
  font: inherit;
  color: var(--text);
  cursor: pointer;
}
.axis-btn + .axis-btn {
  border-left: 1px solid var(--border);
}
.axis-btn.active {
  background: var(--accent);
  color: #fff;
}
.slice-count {
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.toolbar-link {
  padding: 0;
  border: none;
  background: none;
  font: inherit;
  color: var(--accent);
  text-decoration: none;
  cursor: pointer;
}
.slice-count {
  margin-right: auto;
}
.toolbar-link:hover {
  text-decoration: underline;
}

.frame {
  position: relative;
  display: grid;
  place-items: center;
  aspect-ratio: 1;
  border-radius: 8px;
  background: #000;
  overflow: hidden;
  overscroll-behavior: contain;
  cursor: ns-resize;
}
.frame:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.frame img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  user-select: none;
}
.frame-skeleton {
  width: 60%;
  opacity: 0.3;
}
.frame-error {
  position: absolute;
  inset: auto 10px 10px;
  margin: 0;
  padding: 6px 8px;
  border-radius: 6px;
  font-size: 0.8rem;
  color: #fff;
  background: rgba(206, 44, 49, 0.85);
}

.slider {
  width: 100%;
  margin: 0;
  accent-color: var(--accent);
}
</style>
