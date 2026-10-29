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
  position: fixed;
  bottom: 32px;
  right: max(24px, calc(50vw - 210px + 24px));
  width: 56px;
  height: 56px;
  border: none;
  border-radius: 50%;
  background: var(--accent);
  color: white;
  display: grid;
  place-items: center;
  cursor: pointer;
  z-index: 10;
}
.next:disabled {
  opacity: 0.35;
  cursor: default;
}
</style>
