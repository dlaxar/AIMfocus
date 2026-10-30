<template>
  <main class="screen" :class="{ 'has-logo': route.path !== '/' && route.path !== '/patient/dashboard' }">
    <RouterLink v-if="route.path !== '/' && route.path !== '/patient/dashboard'" to="/" class="screen-logo" aria-label="Home">
      <img src="./assets/logo.png" alt="Logo" />
    </RouterLink>
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
  display: flex;
  z-index: 20;
}
.screen-logo img {
  height: 40px;
  width: auto;
}
</style>
