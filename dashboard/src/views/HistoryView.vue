<script setup>
import { ref, computed, onMounted } from 'vue'
import { apiFetch } from '../api/index.js'
import HistoryRow from './HistoryRow.vue'
import GasMeter from '../components/GasMeter.vue'
import LogPanel from '../components/LogPanel.vue'

const jobs = ref([])
const search = ref('')
const statusFilter = ref('all')
const page = ref(0)
const modalJob = ref(null)
const PAGE_SIZE = 20

onMounted(() => {
  apiFetch('/agents/history?limit=200&offset=0').then(data => { jobs.value = data }).catch(() => {})
})

const filtered = computed(() => {
  return jobs.value.filter(j => {
    const matchSearch = !search.value ||
      j.project_name.toLowerCase().includes(search.value.toLowerCase()) ||
      j.task.toLowerCase().includes(search.value.toLowerCase())
    const matchStatus = statusFilter.value === 'all' || j.status === statusFilter.value
    return matchSearch && matchStatus
  })
})

const totalPages = computed(() => Math.ceil(filtered.value.length / PAGE_SIZE))
const paged = computed(() => filtered.value.slice(page.value * PAGE_SIZE, (page.value + 1) * PAGE_SIZE))

function onSearchChange() { page.value = 0 }
function onStatusChange() { page.value = 0 }
</script>

<template>
  <div class="page">
    <div class="page-title">History</div>
    <div class="controls">
      <input placeholder="Search project or task…" v-model="search" @input="onSearchChange" style="width: 220px" />
      <select v-model="statusFilter" @change="onStatusChange">
        <option value="all">All statuses</option>
        <option value="completed">Completed</option>
        <option value="failed">Failed</option>
        <option value="cancelled">Cancelled</option>
        <option value="out_of_gas">Out of gas</option>
      </select>
    </div>

    <div v-if="paged.length === 0" class="empty">No jobs found.</div>
    <div v-else class="card" style="padding: 0; overflow: hidden">
      <table>
        <thead>
          <tr>
            <th>Task</th>
            <th>Project</th>
            <th>Status</th>
            <th>Duration</th>
            <th>Completed</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          <HistoryRow
            v-for="j in paged"
            :key="j.id"
            :job="j"
            @logs-click="modalJob = $event"
          />
        </tbody>
      </table>
    </div>

    <div class="pagination">
      <button :disabled="page === 0" @click="page--">← Prev</button>
      <span>Page {{ page + 1 }} of {{ totalPages || 1 }} · {{ filtered.length }} total</span>
      <button :disabled="page >= totalPages - 1" @click="page++">Next →</button>
    </div>

    <!-- Logs modal -->
    <div v-if="modalJob" class="modal-overlay" @click="modalJob = null">
      <div class="modal" @click.stop>
        <div class="modal-header">
          <span>{{ modalJob.task }} — {{ modalJob.project_name }}</span>
          <button class="close-btn" @click="modalJob = null">✕</button>
        </div>
        <div class="modal-body">
          <GasMeter :job="modalJob" />
          <LogPanel :job-id="modalJob.id" :is-active="false" />
        </div>
      </div>
    </div>
  </div>
</template>
