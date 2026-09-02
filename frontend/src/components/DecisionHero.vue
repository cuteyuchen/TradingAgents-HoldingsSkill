<script setup lang="ts">
import { computed } from 'vue'
import { ShieldAlert, Sparkles } from 'lucide-vue-next'
import { fmtDateTime } from '../utils/ui'

const props = withDefaults(defineProps<{
  action?: string | null
  summary?: string | null
  reasons?: string[]
  checkpoint?: string | null
  finalizedAt?: string | null
  quality?: string | null
  freshness?: string | null
}>(), { action: 'NO_ACTION', summary: '', reasons: () => [], checkpoint: '', finalizedAt: '', quality: '', freshness: '' })

const normalized = computed(() => String(props.action || 'NO_ACTION').toUpperCase())
const label = computed(() => ({
  ACTION: '需要调整',
  NO_ACTION: '暂不操作',
  BLOCKED: '暂不可形成可靠行动',
  DATA_GAP: '数据不完整',
}[normalized.value] || normalized.value))
const tone = computed(() => normalized.value === 'ACTION' ? 'action' : ['BLOCKED', 'DATA_GAP'].includes(normalized.value) ? 'blocked' : 'hold')
</script>

<template>
  <section class="decision-hero" :class="`decision-${tone}`">
    <div class="decision-hero-main">
      <div class="decision-kicker"><Sparkles :size="15" aria-hidden="true" />今日建议</div>
      <h2>{{ label }}</h2>
      <code>{{ normalized }}</code>
      <p class="decision-summary">{{ summary || '当前没有足够的新信息改变组合决策。' }}</p>
      <ul v-if="reasons.length" class="decision-reasons">
        <li v-for="reason in reasons.slice(0, 3)" :key="reason">{{ reason }}</li>
      </ul>
    </div>
    <div class="decision-hero-side">
      <ShieldAlert :size="24" aria-hidden="true" />
      <span v-if="checkpoint">检查点 {{ checkpoint }}</span>
      <span v-if="finalizedAt">{{ fmtDateTime(finalizedAt) }}</span>
      <span v-if="quality">数据质量 {{ quality }}</span>
      <span v-if="freshness">数据新鲜度 {{ freshness }}</span>
    </div>
    <div v-if="$slots.actions || $slots.default" class="decision-hero-footer">
      <slot />
      <slot name="actions" />
    </div>
  </section>
</template>

<style scoped>
.decision-hero { display: grid; grid-template-columns: minmax(0, 1fr) 210px; gap: 22px; border: 1px solid var(--border); border-top: 4px solid var(--primary); border-radius: 8px; background: var(--surface); padding: 22px 24px; box-shadow: var(--shadow-md); }
.decision-action { border-top-color: var(--warning); }.decision-blocked { border-top-color: var(--danger); }.decision-hold { border-top-color: var(--primary); }
.decision-hero-main { min-width: 0; }
.decision-kicker { display: flex; align-items: center; gap: 7px; color: var(--primary); font-size: 12px; font-weight: 800; }
.decision-hero h2 { margin: 10px 0 2px; font-size: 32px; line-height: 1.15; }
.decision-hero code { color: var(--text-muted); font-size: 12px; }
.decision-summary { max-width: 760px; margin: 16px 0 0; font-size: 16px; line-height: 1.7; }
.decision-reasons { display: flex; flex-wrap: wrap; gap: 8px 22px; margin: 16px 0 0; padding-left: 18px; color: var(--text-muted); }
.decision-reasons li { max-width: 340px; }
.decision-hero-side { display: grid; align-content: start; gap: 8px; border-left: 1px solid var(--border); padding-left: 18px; color: var(--text-muted); font-size: 12px; }
.decision-hero-side svg { color: var(--primary); margin-bottom: 4px; }
.decision-hero-footer { grid-column: 1 / -1; display: flex; align-items: center; justify-content: space-between; gap: 12px; border-top: 1px solid var(--border); padding-top: 14px; }
@media (max-width: 700px) { .decision-hero { grid-template-columns: 1fr; padding: 18px; } .decision-hero-side { grid-template-columns: repeat(2, minmax(0, 1fr)); border-top: 1px solid var(--border); border-left: 0; padding: 14px 0 0; } .decision-hero-side svg { display: none; } .decision-hero-footer { grid-column: auto; align-items: flex-start; flex-direction: column; } }
</style>
