<script setup>
import { ref, watch } from 'vue'
import { apiFetch } from '../api/index.js'
import { timeAgo } from '../utils/time.js'

const props = defineProps(['prefill'])
const emit = defineEmits(['launched'])

const projectQuery = ref(props.prefill?.project || '')
const projectResults = ref([])
const selectedProject = ref(null)
const branches = ref([])
const branch = ref(props.prefill?.branch || '')
const mrs = ref([])
const mrIid = ref('')
const goal = ref('')
const gasInput = ref(160000)
const gasOutput = ref(40000)
const launching = ref(false)
const error = ref('')

let searchTimer = null

watch(projectQuery, val => {
  if (!val) { projectResults.value = []; return }
  clearTimeout(searchTimer)
  searchTimer = setTimeout(async () => {
    try {
      const results = await apiFetch(`/projects/search?q=${encodeURIComponent(val)}`)
      projectResults.value = Array.isArray(results) ? results : []
    } catch { projectResults.value = [] }
  }, 300)
})

async function selectProject(p) {
  selectedProject.value = p
  projectQuery.value = p.path_with_namespace || p.name || String(p.id)
  projectResults.value = []
  branch.value = ''
  mrIid.value = ''
  try {
    const bs = await apiFetch(`/projects/${p.id}/branches`)
    branches.value = Array.isArray(bs) ? bs : []
    if (bs && bs.length > 0) branch.value = p.default_branch || bs[0]
  } catch { branches.value = [] }
  try {
    const openMrs = await apiFetch(`/projects/${p.id}/mrs`)
    mrs.value = Array.isArray(openMrs) ? openMrs : []
  } catch { mrs.value = [] }
}

async function handleLaunch() {
  if (!selectedProject.value || !branch.value || !goal.value.trim()) {
    error.value = 'Project, branch, and goal are required.'
    return
  }
  error.value = ''
  launching.value = true
  try {
    const body = {
      project_id: selectedProject.value.id,
      project_path: selectedProject.value.path_with_namespace || selectedProject.value.name || String(selectedProject.value.id),
      branch: branch.value,
      goal: goal.value.trim(),
      mr_iid: mrIid.value ? parseInt(mrIid.value) : null,
      gas_limit_input: gasInput.value,
      gas_limit_output: gasOutput.value,
    }
    const session = await apiFetch('/sessions', { method: 'POST', body: JSON.stringify(body) })
    emit('launched', session)
  } catch (e) {
    error.value = e.message || 'Failed to launch session.'
  } finally {
    launching.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="page-title">New Session</div>
    <div class="session-launcher card">
      <div class="form-group">
        <label class="form-label">Project</label>
        <input
          class="form-input"
          placeholder="Search projects…"
          v-model="projectQuery"
          @input="selectedProject = null"
        />
        <div v-if="projectResults.length" class="search-results">
          <div
            v-for="p in projectResults"
            :key="p.id"
            class="search-result-item"
            @click="selectProject(p)"
          >
            <div>{{ p.path_with_namespace || p.name }}</div>
            <div class="search-result-meta">
              {{ p.namespace?.name || '' }}{{ p.last_activity_at ? ' · ' + timeAgo(p.last_activity_at) : '' }}
            </div>
          </div>
        </div>
      </div>

      <div class="form-row">
        <div class="form-group">
          <label class="form-label">Branch</label>
          <select v-if="branches.length" class="form-input" v-model="branch">
            <option v-for="b in branches" :key="b" :value="b">{{ b }}</option>
          </select>
          <input v-else class="form-input" placeholder="main" v-model="branch" />
        </div>
        <div class="form-group">
          <label class="form-label">Target MR (optional)</label>
          <select class="form-input" v-model="mrIid">
            <option value="">None</option>
            <option v-for="mr in mrs" :key="mr.iid" :value="mr.iid">!{{ mr.iid }} {{ mr.title }}</option>
          </select>
        </div>
      </div>

      <div class="form-group">
        <label class="form-label">Goal</label>
        <textarea class="form-input" placeholder="Describe what you want the agent to do…" v-model="goal" />
      </div>

      <div class="form-row">
        <div class="form-group">
          <label class="form-label">Input gas limit (tokens)</label>
          <input class="form-input" type="number" min="1000" step="10000" v-model.number="gasInput" />
        </div>
        <div class="form-group">
          <label class="form-label">Output gas limit (tokens)</label>
          <input class="form-input" type="number" min="1000" step="5000" v-model.number="gasOutput" />
        </div>
      </div>

      <div v-if="error" style="color: var(--red); font-size: 13px; margin-bottom: 12px">{{ error }}</div>

      <button class="btn-primary" @click="handleLaunch" :disabled="launching || !selectedProject">
        {{ launching ? 'Launching…' : 'Launch Session' }}
      </button>
    </div>
  </div>
</template>
