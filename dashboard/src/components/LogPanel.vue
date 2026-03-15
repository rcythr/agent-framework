<script setup>
import { ref, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { apiFetch } from '../api/index.js'
import LogEvent from './LogEvent.vue'

const props = defineProps(['jobId', 'isActive'])

const events = ref([])
const autoScroll = ref(true)
const scrollEl = ref(null)

let es = null

function cleanup() {
  if (es) { es.close(); es = null }
}

function subscribe() {
  cleanup()
  events.value = []

  if (!props.isActive) {
    apiFetch(`/agents/${props.jobId}/logs`).then(data => { events.value = data }).catch(() => {})
    return
  }

  es = new EventSource(`/agents/${props.jobId}/logs/stream`)
  es.onmessage = e => {
    if (!e.data) return
    try { events.value.push(JSON.parse(e.data)) } catch {}
  }
  es.onerror = () => es.close()
}

watch(() => [props.jobId, props.isActive], subscribe, { immediate: true })

watch(events, async () => {
  if (!autoScroll.value) return
  await nextTick()
  if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight
}, { deep: true })

onUnmounted(cleanup)

function handleScroll() {
  const el = scrollEl.value
  if (!el) return
  autoScroll.value = el.scrollHeight - el.scrollTop - el.clientHeight < 40
}
</script>

<template>
  <div class="log-panel">
    <div class="log-panel-inner" ref="scrollEl" @scroll="handleScroll">
      <div v-if="events.length === 0" style="color: var(--text-muted); font-size: 12px">No events yet.</div>
      <LogEvent
        v-for="(ev, i) in events"
        :key="`${ev.job_id}-${ev.sequence ?? i}`"
        :event="ev"
      />
    </div>
  </div>
</template>
