<template>
  <footer class="app-footer">
    <span>Team Oachkatzlschwoaf</span>
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
  </footer>
</template>

<script setup>
// Injected at build time by vite.config.js
const commit = __COMMIT__

const date = commit.date
  ? new Date(commit.date).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
  : ''
const title = [commit.subject, date].filter(Boolean).join(' — ')
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
</style>
