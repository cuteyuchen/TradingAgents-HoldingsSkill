<script setup lang="ts">
/**
 * Acceptance host for V3InstrumentDetailDrawer lifecycle.
 * Dev / foundation-flag only — not linked from product navigation.
 */
import { ref } from 'vue'
import V3InstrumentDetailDrawer from './V3InstrumentDetailDrawer.vue'

const code = ref('600519.SH')
const open = ref(false)
</script>

<template>
  <div class="drawer-host" data-testid="instrument-drawer-host">
    <h1>Instrument Detail Drawer Host</h1>
    <label>
      代码
      <input v-model="code" data-testid="drawer-host-code" aria-label="标的代码" />
    </label>
    <button type="button" data-testid="drawer-host-open" aria-label="打开标的详情抽屉" @click="open = true">
      打开详情
    </button>
    <button type="button" data-testid="drawer-host-close" aria-label="关闭标的详情抽屉" @click="open = false">
      关闭详情
    </button>
    <V3InstrumentDetailDrawer v-model="open" :code="code" />
  </div>
</template>

<style scoped>
.drawer-host {
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: flex-start;
  max-width: 480px;
}
.drawer-host h1 {
  margin: 0;
  font-size: 18px;
}
</style>
