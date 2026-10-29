<template>
  <button class="next" type="button" aria-label="Next" :disabled="disabled">
    <svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true">
      <path d="M4 12h15m-6-6 6 6-6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
    </svg>
  </button>
</template>

<script setup>
defineProps({ disabled: Boolean })
</script>

<style scoped>
.next {
  align-self: flex-end;
  margin-top: auto;
  width: 56px;
  height: 56px;
  border: none;
  border-radius: 50%;
  background: var(--accent);
  color: white;
  display: grid;
  place-items: center;
  cursor: pointer;
}
.next:disabled {
  opacity: 0.35;
  cursor: default;
}
</style>
