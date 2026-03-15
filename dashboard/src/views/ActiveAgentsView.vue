<script setup>
import { ref, onUnmounted } from 'vue'
import { apiFetch } from '../api/index.js'
import AgentCard from '../components/AgentCard.vue'

const jobs = ref([])
let cancelled = false

async function poll() {
  try {
    const data = await apiFetch('/agents')
    if (!cancelled) jobs.value = data
  } catch {}
  if (!cancelled) setTimeout(poll, 5000)
}
poll()

onUnmounted(() => { cancelled = true })

function removeCancelled(id) {
  jobs.value = jobs.value.filter(j => j.id !== id)
}
</script>

<template>
  <div class="page">
    <div class="page-title">Active Agents</div>
    <div v-if="jobs.length === 0" class="empty">No active agents.</div>
    <AgentCard
      v-for="j in jobs"
      :key="j.id"
      :job="j"
      @cancelled="removeCancelled"
    />
  </div>
</template>
