<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue';
import { mountAIMfocusChase } from './chase.js';
import './chase.css';
const root = ref(null);
let cleanup;
onMounted(() => { cleanup = mountAIMfocusChase(root.value); });
onBeforeUnmount(() => { cleanup?.(); });
</script>

<template>
  <div ref="root" class="aimfocus-chase">
    <canvas width="1400" height="340" role="img"
      aria-label="A squirrel chases an acorn back and forth, turning to face the viewer at each end." />
  </div>
</template>
