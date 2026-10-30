<template>
  <article class="excerpt">
    <header class="excerpt-head">
      <span class="doc-title">{{ ctx?.doc.title ?? source.doc_title ?? 'Source' }}</span>
      <span v-if="docDate" class="doc-date">{{ formatDate(docDate) }}</span>
    </header>
    <div v-if="ctx?.section.name" class="section-name">{{ ctx.section.name }}</div>

    <div class="excerpt-body">
      <p v-if="loading" class="muted">Loading section…</p>
      <HighlightedText
        v-else-if="ctx"
        :text="ctx.section.text"
        :offset="ctx.section.start"
        :start="start"
        :end="end"
        scroll-to-mark
      />
      <q v-else-if="source.quote_de" class="quote">{{ source.quote_de }}</q>
    </div>

    <button v-if="docId" type="button" class="link-btn" @click="openDocument(docId, start, end)">
      Show in whole letter
      <svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
    </button>
  </article>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import HighlightedText from './HighlightedText.vue'
import { getPassageContext } from '../../api.js'
import { formatDate } from './format.js'
import { openDocument } from './viewer.js'

// One source passage shown inside its document section. `source` is a screen Source, or just
// {passage_id} (contradictions only name passages).
const props = defineProps({ source: { type: Object, required: true } })

const ctx = ref(null)
const loading = ref(false)

watch(
  () => props.source.passage_id,
  async (pid) => {
    ctx.value = null
    if (!pid) return
    loading.value = true
    try {
      ctx.value = await getPassageContext(pid)
    } catch {
      ctx.value = null // fall back to the quote
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)

const docId = computed(() => ctx.value?.doc.doc_id ?? props.source.doc_id)
const docDate = computed(() => ctx.value?.doc.date ?? props.source.doc_date)
const start = computed(() => props.source.start ?? ctx.value?.passage.start)
const end = computed(() => props.source.end ?? ctx.value?.passage.end)
</script>

<style scoped>
.excerpt + .excerpt {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}
.excerpt-head {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 8px;
  align-items: baseline;
}
.doc-title {
  font-weight: 600;
}
.doc-date,
.section-name,
.muted {
  color: var(--muted);
  font-size: 0.78rem;
}
.section-name {
  margin-top: 1px;
}
.excerpt-body {
  margin: 6px 0;
  max-height: 180px;
  overflow-y: auto;
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--bg);
  font-size: 0.8rem;
}
.excerpt-body .muted {
  margin: 0;
}
.quote {
  font-style: italic;
}
.link-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: none;
  background: none;
  padding: 0;
  font: inherit;
  font-size: 0.78rem;
  color: var(--accent);
  cursor: pointer;
}
.link-btn:hover {
  text-decoration: underline;
}
</style>
