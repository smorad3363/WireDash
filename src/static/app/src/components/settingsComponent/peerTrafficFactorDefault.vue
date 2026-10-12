<script setup>
import {onMounted, ref, computed} from "vue";
import {readTrafficFactor, saveTrafficFactor, validTrafficFactor} from "@/utilities/peerTrafficDefaults.mjs";
const factor = ref(1);
const status = ref("");
const valid = computed(() => validTrafficFactor(factor.value));
onMounted(() => {factor.value = readTrafficFactor();});
const save = () => {
  try {
    saveTrafficFactor(factor.value);
    status.value = "Saved on this browser for newly created peers with a data quota.";
  } catch(e) {
    status.value = e?.message || "Could not save preference in this browser.";
  }
};
</script>

<template>
  <div class="border-top pt-3 mt-3">
    <label for="default-traffic-weight" class="form-label fw-semibold">Default traffic factor</label>
    <div class="d-flex align-items-center gap-2 flex-wrap">
      <input id="default-traffic-weight" class="form-control" style="max-width:180px"
        type="number" min="0.1" max="10" step="0.1" v-model.number="factor"
        :aria-invalid="!valid">
      <button class="btn btn-primary" type="button" :disabled="!valid" @click="save">
        Save default
      </button>
    </div>
    <small class="text-muted d-block mt-2">
      Applies automatically to NEW peers with a data quota (including bulk-created peers).
      Factor 2 bills 2 GB for every 1 GB of actual upload + download.
      The existing server API cannot store a shared global default: this
      preference is saved only in this admin browser.
    </small>
    <small class="text-muted d-block mt-1">
      To update EXISTING peers together, use Select Peers → Set factor.
      Their quota must already be configured. No existing peer is changed by
      saving this default.
    </small>
    <div v-if="status" class="small mt-2" role="status">{{status}}</div>
  </div>
</template>
