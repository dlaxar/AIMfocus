<template>
  <h1>Database</h1>

  <p v-if="error" class="error" role="alert">{{ error }}</p>
  <p v-else-if="!stats" class="muted">Validating FHIR bundles…</p>

  <template v-else>
    <div class="tiles">
      <div class="tile">
        <span class="value">{{ stats.patients }}</span>
        <span class="label">Patients</span>
      </div>
      <div class="tile">
        <span class="value">{{ stats.valid_documents }}</span>
        <span class="label">Validated FHIR documents</span>
        <span v-if="stats.valid_documents !== stats.documents" class="sub">of {{ stats.documents }}</span>
      </div>
      <div class="tile">
        <span class="value">{{ stats.valid_bundles }}<small> / {{ stats.bundles }}</small></span>
        <span class="label">Valid FHIR bundles (R4)</span>
      </div>
    </div>

    <table>
      <thead>
        <tr>
          <th>Patient</th>
          <th class="num">Documents</th>
          <th class="num">Resources</th>
          <th>FHIR R4</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="p in stats.per_patient" :key="p.patient_id">
          <td>
            {{ p.name || '—' }}
            <span class="muted id">{{ p.patient_id }}</span>
          </td>
          <td class="num">{{ p.documents }}</td>
          <td class="num">{{ p.resources }}</td>
          <td>
            <span v-if="p.valid" class="ok">Valid</span>
            <span v-else class="bad" :title="p.error">Invalid</span>
          </td>
        </tr>
      </tbody>
    </table>
  </template>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { getStats } from '../api.js'

const stats = ref(null)
const error = ref('')

onMounted(async () => {
  try {
    stats.value = await getStats()
  } catch (e) {
    error.value = e.message || 'Could not load stats'
  }
})
</script>

<style scoped>
.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 16px;
}
.tile {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 20px;
  background: var(--surface);
  border: 1.5px solid var(--border);
  border-radius: 12px;
}
.value {
  font-size: 2.25rem;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.value small {
  font-size: 1.25rem;
  color: var(--muted);
}
.label {
  color: var(--muted);
}
.sub {
  font-size: 0.875rem;
  color: var(--muted);
}
table {
  width: 100%;
  border-collapse: collapse;
  background: var(--surface);
  border: 1.5px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
}
th,
td {
  padding: 10px 14px;
  text-align: left;
  border-bottom: 1px solid var(--border);
}
tbody tr:last-child td {
  border-bottom: none;
}
th {
  font-weight: 500;
  color: var(--muted);
}
.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.id {
  margin-left: 6px;
  font-size: 0.8125rem;
}
.muted {
  color: var(--muted);
}
.ok {
  color: var(--accent);
}
.bad {
  color: #c62828;
  cursor: help;
}
.error {
  color: #c62828;
}
</style>
