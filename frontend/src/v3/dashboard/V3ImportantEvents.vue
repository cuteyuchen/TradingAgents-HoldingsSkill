<script setup lang="ts">
import V3EmptyState from '../components/V3EmptyState.vue'
import type { V3ImportantEventsVM } from './dashboard-types'

defineProps<{
  events: V3ImportantEventsVM
}>()
</script>

<template>
  <section class="events" data-testid="v3-important-events" aria-labelledby="events-title">
    <header class="events__header">
      <h2 id="events-title" class="events__title">重要事件 / 下一检查点</h2>
      <p class="events__desc">只展示与决策相关的检查点、告警与触发状态。</p>
    </header>

    <div v-if="events.nextCheckpoint" class="events__checkpoint" data-testid="v3-next-checkpoint">
      <span class="events__checkpoint-label">下一检查点</span>
      <strong>{{ events.nextCheckpoint.label }}</strong>
      <span v-if="events.nextCheckpoint.time" class="v3-number">{{ events.nextCheckpoint.time }}</span>
      <span v-if="events.nextCheckpoint.detail" class="events__detail">{{ events.nextCheckpoint.detail }}</span>
    </div>

    <div class="events__columns">
      <div v-if="events.warnings.length" data-testid="v3-event-warnings">
        <h3>警告</h3>
        <ul>
          <li v-for="item in events.warnings.slice(0, 4)" :key="item.key">
            <span v-if="item.time" class="v3-number">{{ item.time }}</span>
            {{ item.label }}
          </li>
        </ul>
      </div>
      <div v-if="events.triggers.length" data-testid="v3-event-triggers">
        <h3>触发状态</h3>
        <ul>
          <li v-for="item in events.triggers.slice(0, 4)" :key="item.key">
            <span v-if="item.time" class="v3-number">{{ item.time }}</span>
            {{ item.label }}
            <small v-if="item.detail">{{ item.detail }}</small>
          </li>
        </ul>
      </div>
      <div v-if="events.notifications.length" data-testid="v3-event-notifications">
        <h3>运营通知</h3>
        <ul>
          <li v-for="item in events.notifications.slice(0, 3)" :key="item.key">
            <span v-if="item.time" class="v3-number">{{ item.time }}</span>
            {{ item.label }}
          </li>
        </ul>
      </div>
    </div>

    <V3EmptyState
      v-if="!events.nextCheckpoint && !events.warnings.length && !events.triggers.length && !events.notifications.length"
      title="暂无重要事件"
      description="当前没有需要立刻关注的检查点或告警。"
      data-testid="v3-events-empty"
    />
  </section>
</template>

<style scoped>
.events__header { margin-bottom: var(--v3-space-3); }
.events__title { margin: 0; font-size: var(--v3-font-size-xl); font-weight: 700; }
.events__desc { margin: var(--v3-space-1) 0 0; color: var(--v3-text-muted); font-size: var(--v3-font-size-sm); }
.events__checkpoint {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--v3-space-2);
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-md);
  background: var(--v3-primary-soft);
  padding: var(--v3-space-3);
  margin-bottom: var(--v3-space-3);
}
.events__checkpoint-label {
  color: var(--v3-primary);
  font-size: var(--v3-font-size-xs);
  font-weight: 800;
  letter-spacing: 0.04em;
}
.events__detail { color: var(--v3-text-muted); font-size: var(--v3-font-size-sm); }
.events__columns {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--v3-space-3);
}
.events__columns h3 {
  margin: 0 0 var(--v3-space-2);
  font-size: var(--v3-font-size-md);
  font-weight: 700;
}
.events__columns ul {
  margin: 0;
  padding-left: 18px;
  color: var(--v3-text-secondary);
  font-size: var(--v3-font-size-sm);
}
.events__columns small { color: var(--v3-text-muted); margin-left: 6px; }
@media (max-width: 800px) {
  .events__columns { grid-template-columns: 1fr; }
}
</style>
