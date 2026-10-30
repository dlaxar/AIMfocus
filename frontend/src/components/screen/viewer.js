import { reactive } from 'vue'

// The full-letter viewer is one drawer per page; any source can open it at a passage.
export const docViewer = reactive({ open: false, docId: null, start: null, end: null })

export function openDocument(docId, start = null, end = null) {
  Object.assign(docViewer, { open: true, docId, start, end })
}

export function closeDocument() {
  docViewer.open = false
}
