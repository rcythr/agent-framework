<script setup>
import { apiFetch } from '../api/index.js'
import { duration, timeAgo } from '../utils/time.js'
import StatusPill from '../components/StatusPill.vue'

const props = defineProps(['job'])
const emit = defineEmits(['logsClick'])

async function handleRetry() {
  try {
    await apiFetch('/trigger', {
      method: 'POST',
      body: JSON.stringify({ task: props.job.task, project_id: props.job.project_id, context: props.job.context }),
    })
  } catch {}
}
</script>

<template>
  <tr>
    <td>{{ job.task }}</td>
    <td>{{ job.project_name }}</td>
    <td><StatusPill :status="job.status" /></td>
    <td>{{ duration(job.started_at, job.finished_at) }}</td>
    <td>{{ timeAgo(job.finished_at) }}</td>
    <td>
      <div style="display: flex; gap: 6px">
        <button class="btn-sm btn-primary" @click="emit('logsClick', job)">Logs</button>
        <button v-if="job.status === 'failed'" class="btn-retry btn-sm" @click="handleRetry">Retry</button>
      </div>
    </td>
  </tr>
</template>
