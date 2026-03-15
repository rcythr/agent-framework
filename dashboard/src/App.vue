<script setup>
import { ref } from 'vue'
import ActiveAgentsView from './views/ActiveAgentsView.vue'
import HistoryView from './views/HistoryView.vue'
import NewSessionView from './views/NewSessionView.vue'

const view = ref('active')

const tabs = [
  ['active', 'Active Agents'],
  ['history', 'History'],
  ['session', 'New Session'],
]
</script>

<template>
  <nav class="nav">
    <div class="nav-logo">⚡ Phalanx</div>
    <div
      v-for="[v, label] in tabs"
      :key="v"
      :class="['nav-item', view === v ? 'active' : '']"
      @click="view = v"
    >
      {{ label }}
    </div>
  </nav>
  <ActiveAgentsView v-if="view === 'active'" />
  <HistoryView v-else-if="view === 'history'" />
  <NewSessionView v-else-if="view === 'session'" />
</template>
