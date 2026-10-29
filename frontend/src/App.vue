<template>
  <main class="screen" :class="{ 'has-logo': route.path !== '/' }">
    <img v-if="route.path !== '/'" src="./assets/logo.png" alt="Logo" class="screen-logo" />
    <RouterView />
  </main>
</template>

<script setup>
import { useRoute } from 'vue-router'
const route = useRoute()
</script>

<style scoped>
.screen {
  position: relative;
}
.screen.has-logo {
  padding-top: 72px;
}
.screen-logo {
  position: absolute;
  top: 16px;
  left: 7px;
  height: 40px;
  width: auto;
}
</style>
