<template>
  <footer class="app-footer">
    <span>Built on FHIR with Oachkatzlschwoaf</span>
    <span aria-hidden="true">·</span>
    <span v-if="!commit.shortHash">unknown commit</span>
    <a
      v-else-if="commit.url"
      :href="`${commit.url}/commit/${commit.hash}`"
      :title="title"
      target="_blank"
      rel="noopener"
    >{{ commit.shortHash }}</a>
    <span v-else :title="title">{{ commit.shortHash }}</span>
    <template v-for="tag in tags" :key="tag">
      <a
        v-if="commit.url"
        class="tag"
        :href="`${commit.url}/tree/${encodeURIComponent(tag)}`"
        target="_blank"
        rel="noopener"
      >{{ tag }}</a>
      <span v-else class="tag">{{ tag }}</span>
    </template>
  </footer>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { getHealth } from '../api.js'

// Injected at build time by vite.config.js
const commit = __COMMIT__

const date = commit.date
  ? new Date(commit.date).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
  : ''
const title = [commit.subject, date].filter(Boolean).join(' — ')

// Tags come from the core at runtime (they can be added after the build), and only count
// when the core runs the same commit this bundle was built from
const tags = ref([])
onMounted(async () => {
  try {
    const { commit: core } = await getHealth()
    if (core?.hash && core.hash === commit.hash) tags.value = core.tags ?? []
  } catch {
    // No tags when the core is unreachable
  }
})
</script>

<style scoped>
.app-footer {
  margin-top: auto;
  /* Same height as the fixed Next button, so the two line up at the bottom */
  min-height: 56px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font-size: 0.75rem;
  color: var(--muted);
}
.app-footer a {
  color: inherit;
  font-family: ui-monospace, monospace;
}
.app-footer a:hover {
  color: var(--accent);
}
.app-footer .tag {
  padding: 1px 6px;
  border: 1px solid currentColor;
  border-radius: 999px;
  font-family: ui-monospace, monospace;
  text-decoration: none;
}
</style>
