<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { apiFetch } from '../api/index.js'
import { elapsed, copyText } from '../utils/time.js'
import StatusPill from './StatusPill.vue'
import GasMeter from './GasMeter.vue'
import LogPanel from './LogPanel.vue'

const props = defineProps(['job'])
const emit = defineEmits(['cancelled'])

const job = ref({ ...props.job })
const expanded = ref(false)
const cancelling = ref(false)
const lastLog = ref('')
const tick = ref(0)

let tickTimer = null
let es = null

onMounted(() => {
  tickTimer = setInterval(() => tick.value++, 1000)

  es = new EventSource(`/agents/${job.value.id}/logs/stream`)
  es.onmessage = e => {
    if (!e.data) return
    try {
      const ev = JSON.parse(e.data)
      if (ev.event_type && ev.event_type !== 'gas_updated') {
        const p = ev.payload
        const preview = p?.content || p?.message || p?.tool || ev.event_type
        lastLog.value = String(preview).slice(0, 100)
      }
      if (ev.event_type === 'gas_updated') {
        const p = ev.payload
        job.value = {
          ...job.value,
          gas_used_input: p.gas_used_input ?? job.value.gas_used_input,
          gas_used_output: p.gas_used_output ?? job.value.gas_used_output,
          gas_limit_input: p.gas_limit_input ?? job.value.gas_limit_input,
          gas_limit_output: p.gas_limit_output ?? job.value.gas_limit_output,
        }
      }
      if (ev.event_type === 'out_of_gas') {
        job.value = { ...job.value, status: 'out_of_gas' }
      }
    } catch {}
  }
  es.onerror = () => es.close()
})

onUnmounted(() => {
  clearInterval(tickTimer)
  if (es) es.close()
})

const truncId = job.value.id.length > 20
  ? job.value.id.slice(0, 8) + '…' + job.value.id.slice(-8)
  : job.value.id

async function handleCancel(e) {
  e.stopPropagation()
  cancelling.value = true
  job.value = { ...job.value, status: 'cancelled' }
  try {
    await apiFetch(`/agents/${job.value.id}/cancel`, { method: 'POST' })
    emit('cancelled', job.value.id)
  } catch {
    job.value = { ...job.value, status: props.job.status }
    cancelling.value = false
  }
}

async function refreshGas() {
  try {
    const data = await apiFetch(`/agents/${job.value.id}/gas`)
    job.value = { ...job.value, ...data, status: data.status || job.value.status }
  } catch {}
}
</script>

<template>
  <div class="card">
    <div class="card-header" @click="expanded = !expanded">
      <span :class="`dot dot-${job.status}`" />
      <div style="flex: 1">
        <div style="display: flex; align-items: center; gap: 10px">
          <span class="card-title">{{ job.task }}</span>
          <span class="card-meta">project {{ job.project_name }}</span>
        </div>
        <div style="display: flex; align-items: center; gap: 10px; margin-top: 3px">
          <span class="job-id-cell">
            {{ truncId }}
            <button class="copy-btn" @click.stop="copyText(job.id)">⎘</button>
          </span>
          <span class="elapsed">{{ elapsed(job.started_at) }}</span>
          <span v-if="lastLog" class="log-preview">{{ lastLog }}</span>
        </div>
      </div>
      <StatusPill :status="job.status" />
      <button
        v-if="job.status === 'pending' || job.status === 'running'"
        class="btn-cancel btn-sm"
        @click="handleCancel"
        :disabled="cancelling"
      >
        {{ cancelling ? '…' : 'Cancel' }}
      </button>
    </div>

    <GasMeter :job="job" @gas-added="refreshGas" />

    <LogPanel
      v-if="expanded"
      :job-id="job.id"
      :is-active="job.status === 'running' || job.status === 'pending'"
    />
  </div>
</template>
