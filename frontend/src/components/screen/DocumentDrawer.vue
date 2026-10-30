<template>
  <Teleport to="body">
    <Transition name="drawer">
      <div v-if="docViewer.open" class="drawer-backdrop" @click.self="closeDocument">
        <aside class="drawer" role="dialog" aria-modal="true" :aria-label="doc?.title ?? 'Document'">
          <header class="drawer-head">
            <div class="drawer-title">
              <strong>{{ doc?.title ?? 'Loading…' }}</strong>
              <span v-if="doc" class="drawer-meta">
                {{ [formatDate(doc.date), doc.type, doc.department].filter(Boolean).join(' · ') }}
              </span>
            </div>
            <button type="button" class="icon-btn" aria-label="Close" @click="closeDocument">
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
            </button>
          </header>
          <div class="drawer-body">
            <p v-if="error" class="error">{{ error }}</p>
            <HighlightedText
              v-else-if="doc"
              :text="doc.text"
              :start="docViewer.start"
              :end="docViewer.end"
              scroll-to-mark
            />
          </div>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import HighlightedText from './HighlightedText.vue'
import { getDocument } from '../../api.js'
import { formatDate } from './format.js'
import { docViewer, closeDocument } from './viewer.js'

// The whole letter, with the cited passage highlighted and scrolled into view.
const doc = ref(null)
const error = ref('')

watch(
  () => docViewer.open && docViewer.docId,
  async (docId) => {
    if (!docId) return
    if (doc.value?.doc_id === docId) return
    doc.value = null
    error.value = ''
    try {
      doc.value = await getDocument(docId)
    } catch (e) {
      error.value = e.message
    }
  },
  { immediate: true },
)

function onKey(e) {
  if (e.key === 'Escape' && docViewer.open) closeDocument()
}
onMounted(() => document.addEventListener('keydown', onKey))
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKey)
  closeDocument()
})
</script>

<style scoped>
.drawer-backdrop {
  position: fixed;
  inset: 0;
  z-index: 200;
  background: rgba(28, 32, 36, 0.28);
  display: flex;
  justify-content: flex-end;
}
.drawer {
  width: min(640px, 100vw);
  height: 100%;
  background: var(--surface);
  display: flex;
  flex-direction: column;
  box-shadow: -8px 0 28px rgba(28, 32, 36, 0.18);
}
.drawer-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 20px 12px;
  border-bottom: 1px solid var(--border);
}
.drawer-title {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.drawer-meta {
  font-size: 0.8rem;
  color: var(--muted);
}
.drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px 32px;
  font-size: 0.88rem;
  line-height: 1.55;
}
.error {
  color: #ce2c31;
}
.drawer-enter-active,
.drawer-leave-active {
  transition: opacity 0.18s;
}
.drawer-enter-active .drawer,
.drawer-leave-active .drawer {
  transition: transform 0.18s;
}
.drawer-enter-from,
.drawer-leave-to {
  opacity: 0;
}
.drawer-enter-from .drawer,
.drawer-leave-to .drawer {
  transform: translateX(24px);
}
</style>
