<script setup>
import {computed, ref, watch} from "vue";
import {DashboardConfigurationStore} from "@/stores/DashboardConfigurationStore.js";
import {fetchPost} from "@/utilities/fetch.js";
const store = DashboardConfigurationStore();
const current = computed(() => Number(store.Configuration?.Peers?.peer_traffic_factor ?? 1));
const value = ref(current.value);
const confirm = ref(false);
const loading = ref(false);
const message = ref("");
const error = ref("");
watch(current, updated => {if (!loading.value) value.value = updated;});
const valid = computed(() => value.value !== "" && Number.isFinite(Number(value.value)) &&
  Number(value.value) >= 0.1 && Number(value.value) <= 10);
const changed = computed(() => valid.value && Number(value.value) !== current.value);
const save = async () => {
  if (!valid.value || !changed.value || !confirm.value || loading.value) return;
  loading.value = true;
  message.value = ""; error.value = "";
  try {
    let response;
    await fetchPost("/api/updateDashboardConfigurationItem", {
      section:"Peers", key:"peer_traffic_factor",
      value:String(Number(value.value))
    }, res => {response = res;});
    if (!response?.status) throw new Error(response?.message || "Cannot save global factor.");
    store.Configuration.Peers.peer_traffic_factor = String(Number(value.value));
    confirm.value = false;
    message.value = "Saved on server. All existing peers now use the new factor for future traffic only.";
  } catch(e) {error.value = e?.message || "Could not save factor."; }
  finally {loading.value = false;}
};
</script>

<template>
  <section class="border-top pt-3 mt-3" aria-label="Global traffic factor">
    <label for="default-traffic-weight" class="form-label fw-semibold">Global traffic factor</label>
    <div class="d-flex align-items-center gap-2 flex-wrap">
      <input id="default-traffic-weight" class="form-control" style="max-width:180px"
        type="number" min="0.1" max="10" step="0.1" v-model.number="value"
        :disabled="loading" :aria-invalid="!valid">
      <span class="small text-muted">Current: {{current}}×</span>
    </div>
    <small class="text-muted d-block mt-2">
      Stored on the SERVER and included in backups. New peers and bots that omit
      traffic_factor inherit this default. An explicitly submitted factor wins.
    </small>
    <p class="small text-warning-emphasis mt-2 mb-2">
      Changing this value updates ALL existing peers (including restricted peers).
      Previous billed consumption remains unchanged; ONLY NEW upload/download uses
      the new factor. Quota limits stay the same.
    </p>
    <label class="small d-flex gap-2 align-items-start mb-2">
      <input type="checkbox" v-model="confirm" :disabled="loading || !changed">
      I understand this will update every existing peer's factor.
    </label>
    <button class="btn btn-primary btn-sm" type="button"
      :disabled="!changed || !confirm || loading" @click="save">
      {{loading ? "Applying…" : "Save global factor"}}
    </button>
    <div v-if="message" class="text-success small mt-2" role="status">{{message}}</div>
    <div v-if="error" class="text-danger small mt-2" role="alert">{{error}}</div>
  </section>
</template>
