<template>
  <div class="fact-card">
    <span class="fact-label">{{ label }}</span>
    <span class="fact-value" :class="{ placeholder: !value }">
      {{ value ?? '—' }}
    </span>
    <slot />
  </div>
</template>

<script setup>
defineProps({
  /** Short label shown above the value, e.g. "Date of birth" */
  label: { type: String, required: true },
  /** The fact value to display. Pass null/undefined to show a dash. */
  value: { type: [String, Number], default: null },
})
</script>

<style scoped>
.fact-card {
  background: var(--surface);
  border: 1.5px solid var(--border);
  border-radius: 14px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.fact-label {
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
}

.fact-value {
  font-size: 1rem;
  font-weight: 500;
  color: var(--text);
  word-break: break-word;
}

.fact-value.placeholder {
  color: var(--muted);
  font-style: italic;
}
</style>
