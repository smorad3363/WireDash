<script setup>
import {computed, onMounted, ref} from "vue";
import {DashboardConfigurationStore} from "@/stores/DashboardConfigurationStore.js";
import {fetchPost} from "@/utilities/fetch.js";

const store = DashboardConfigurationStore();
const factor = ref("1.0");
const confirm = ref(false);
const busy = ref(false);
const status = ref("");
const ok = computed(() => {
  const n = Number(factor.value);
  return factor.value !== "" && Number.isFinite(n) && n >= 0.1 && n <= 10;
});
const current = computed(() => Number(store.Configuration?.Peers?.peer_traffic_factor || 1));
onMounted(() => {factor.value = String(current.value);});
const save = async () => {
  if (!ok.value || !confirm.value || busy.value) return;
  busy.value = true;
  status.value = "";
  try {
    let reply = null;
    await fetchPost("/api/updateDashboardConfigurationItem", {
      section:"Peers", key:"peer_traffic_factor", value:Number(factor.value)
    }, res => {reply = res;});
    if (!reply?.status) throw new Error(reply?.message || "Cannot update server factor.");
    store.Configuration.Peers.peer_traffic_factor = reply.data;
    factor.value = String(reply.data);
    status.value = "Saved server-wide. Old billed traffic is preserved; only future traffic uses the new factor.";
    confirm.value = false;
  } catch(e) {
    status.value = e?.message || "Server factor change failed. Previous value is kept.";
  } finally { busy.value = false; }
};
</script>

<template>
  <div class="border-top pt-3 mt-3">
    <label class="form-label fw-semibold" for="default-traffic-weight">Global traffic factor</label>
    <p class="text-muted small mb-2">
      Server-wide setting for all current and new peers, including users created
      by bots through the unchanged API. An explicitly supplied API factor
      overrides the default at creation.
    </p>
    <div class="d-flex align-items-center gap-2 flex-wrap">
      <input id="default-traffic-weight" class="form-control" style="max-width:180px"
        type="number" min="0.1" max="10" step="0.1" v-model.number="factor"
        :aria-invalid="!ok" :disabled="busy">
      <span class="text-muted small">Current: {{current}}×</span>
    </div>
    <div class="alert alert-warning small mt-3 mb-2">
      Applies to ALL existing users, including previously configured
      quota factors. Past billed traffic stays unchanged; only traffic
      consumed AFTER this change is billed at the new factor.
      Quotas themselves are not reset. Review any API-created special factors
      before confirming.
    </div>
    <label class="d-flex align-items-center gap-2 small">
      <input type="checkbox" v-model="confirm" :disabled="busy">
      Change every existing user's factor and save this default on the server.
    </label>
    <button class="btn btn-primary mt-2" type="button"
      :disabled="!ok || !confirm || busy" @click="save">
      {{busy ? "Saving…" : "Save global factor"}}
    </button>
    <div v-if="status" class="small mt-2" role="status">{{status}}</div>
  </div>
</template>
