<script setup lang="ts">
const version = import.meta.env.VITE_APP_VERSION
</script>

<template>
  <span class="app-version" :aria-label="`系统版本 ${version}`" :title="`系统版本 ${version}`" data-testid="app-version">
    v{{ version }}
  </span>
</template>

<style scoped>
.app-version {
  display: inline-flex;
  flex-shrink: 0;
  color: var(--v3-text-muted, var(--app-text-muted));
  font-size: 12px;
  font-weight: 400;
  font-variant-numeric: tabular-nums;
  line-height: 1.5;
  white-space: nowrap;
}
</style>
