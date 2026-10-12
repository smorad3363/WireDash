<script setup>
import {computed, onMounted, ref, watch} from "vue";
import {v4 as uuid} from "uuid";
import {fetchGet, fetchPost} from "@/utilities/fetch.js";
import {DEFAULT_SERVER_TIME_ZONE, filterAndSortPeers, peerPolicy, plannedJob, plannedFactorJob, validTimeZone} from "@/utilities/peerBulkPolicy.mjs";
import {readTrafficFactor} from "@/utilities/peerTrafficDefaults.mjs";
import {renewRestrictedWithRestore} from "@/utilities/peerBulkRenewal.mjs";

const props = defineProps({configuration: {type: Object, required: true}});
const emit = defineEmits(["close", "refresh"]);
const records = ref([]);
const loadedAt = ref(0);
const groups = ref({});
const loading = ref(false);
const busy = ref(false);
const error = ref("");
const results = ref([]);
const progress = ref("");
const query = ref("");
const statusFilter = ref("all");
const sort = ref("name");
const sortDirection = ref("asc");
const page = ref(1);
const perPage = 50;
const selected = ref([]);
const action = ref("");
const confirmIds = ref([]);
const confirmation = ref("");
const amount = ref(10);
const chosenTag = ref("");
const serverTimeZone = ref(DEFAULT_SERVER_TIME_ZONE);
const assumeWeightOne = ref(false);
const temporaryAccessConsent = ref(false);
const factorValue = ref(1);
const factorConsent = ref(false);
const now = ref(Date.now());
const filtered = computed(() => filterAndSortPeers(records.value,
  {query: query.value, status: statusFilter.value, sort: sort.value,
    direction: sortDirection.value, serverTimeZone: serverTimeZone.value}, now.value));
const pageCount = computed(() => Math.max(1, Math.ceil(filtered.value.length / perPage)));
const visible = computed(() => filtered.value.slice((page.value - 1) * perPage, page.value * perPage));
const chosenPeers = computed(() => {
  const s = new Set(selected.value);
  return records.value.filter(p => s.has(p.id));
});
const selectedOnPage = computed(() =>
  visible.value.length > 0 && visible.value.every(x => selected.value.includes(x.peer.id)));
const selectedFiltered = computed(() =>
  filtered.value.length > 0 && filtered.value.every(x => selected.value.includes(x.peer.id)));
const actionLabel = computed(() => ({
  restrict: "Restrict access", unrestrict: "Allow access",
  time: "Extend expiry", traffic: "Add traffic quota",
  tag_add: "Add tag", tag_remove: "Remove tag", factor:"Set traffic factor", delete: "Delete peers"
})[action.value] || "");
const tagOptions = computed(() => Object.entries(groups.value || {})
  .map(([id, item]) => ({id, name: item.GroupName || id})));
const maxOperation = 200;
const refresh = async () => {
  if (busy.value) return;
  loading.value = true;
  error.value = "";
  let response = null;
  try {
    await fetchGet("/api/getWireguardConfigurationInfo",
      {configurationName: props.configuration.Name}, res => { response = res; });
    if (!response?.status || !Array.isArray(response.data?.configurationPeers) ||
      !Array.isArray(response.data?.configurationRestrictedPeers)) {
      throw new Error(response?.message || "Cannot read existing peer list.");
    }
    const peers = [
      ...response.data.configurationPeers.map(x => ({...x, restricted: false})),
      ...response.data.configurationRestrictedPeers.map(x => ({...x, restricted: true}))
    ];
    const ids = new Set(peers.map(x => x.id));
    records.value = peers;
    groups.value = response.data.configurationInfo?.Info?.PeerGroups || {};
    selected.value = selected.value.filter(id => ids.has(id));
    loadedAt.value = Date.now();
    now.value = Date.now();
  } catch (e) {
    error.value = e?.message || "Unable to load peer information.";
  } finally {
    loading.value = false;
  }
};
onMounted(refresh);

watch([query, statusFilter, sort, sortDirection, serverTimeZone], () => {page.value = 1;});
// Table headers are the only sort controls. Re-click to reverse; one active sort.
const toggleSort = key => {
  if (busy.value || action.value) return;
  if (sort.value === key) sortDirection.value = sortDirection.value === "asc" ? "desc" : "asc";
  else {sort.value = key; sortDirection.value = key === "usage" ? "desc" : "asc";}
};
const ariaSort = key => sort.value === key ?
  (sortDirection.value === "asc" ? "ascending" : "descending") : "none";
const sortSymbol = key => sort.value !== key ? "↕" :
  sortDirection.value === "asc" ? "▲" : "▼";
watch(pageCount, total => { if (page.value > total) page.value = total; });

const toggle = (id, enabled) => {
  if (busy.value || action.value) return;
  if (enabled) {
    if (!selected.value.includes(id)) selected.value = [...selected.value, id];
  } else selected.value = selected.value.filter(x => x !== id);
};
const togglePage = checked => {
  if (busy.value || action.value) return;
  const pageIds = new Set(visible.value.map(x => x.peer.id));
  selected.value = checked ?
    [...new Set([...selected.value, ...pageIds])] :
    selected.value.filter(x => !pageIds.has(x));
};
const toggleFiltered = checked => {
  if (busy.value || action.value) return;
  const ids = new Set(filtered.value.map(x => x.peer.id));
  selected.value = checked ?
    [...new Set([...selected.value, ...ids])] :
    selected.value.filter(x => !ids.has(x));
};
const openAction = name => {
  if (busy.value || !selected.value.length) return;
  if (selected.value.length > maxOperation) {
    error.value = `For safety, each operation is limited to ${maxOperation} peers. Narrow the filter or selection.`;
    return;
  }
  results.value = [];
  error.value = "";
  action.value = name;
  confirmIds.value = [...selected.value];
  confirmation.value = "";
  amount.value = 10;
  chosenTag.value = tagOptions.value[0]?.id || "";
  assumeWeightOne.value = false;
  temporaryAccessConsent.value = false;
  factorConsent.value = false;
  factorValue.value = readTrafficFactor();
};
const cancelAction = () => {
  if (busy.value) return;
  action.value = "";
  confirmIds.value = [];
  confirmation.value = "";
};
const previewPeers = computed(() => {
  const ids = new Set(confirmIds.value);
  return records.value.filter(p => ids.has(p.id));
});
// All selected peers get a BEFORE/AFTER preview. An ambiguous policy
// disables the write, rather than partially updating an unknown quota.
const renewalPreview = computed(() => {
  if (action.value !== "time" && action.value !== "traffic" && action.value !== "factor") return [];
  return previewPeers.value.map(peer => {
    try {
      const job = action.value === "factor"
        ? plannedFactorJob(peer, props.configuration.Name, factorValue.value)
        : plannedJob(peer, props.configuration.Name, action.value,
            amount.value, new Date(now.value), () => "preview", {
              serverTimeZone:serverTimeZone.value,
              assumeWeightOne:assumeWeightOne.value
            });
      const result = action.value === "time"
        ? `New expiry: ${job.Value} (${serverTimeZone.value})`
        : action.value === "factor"
          ? `Factor: ${peerPolicy(peer).weight}× → ${factorValue.value}× · quota stays ${JSON.parse(job.Value).limit_gb} GB · billed so far stays ${Number(peer.metered_data || 0).toFixed(2)} GB · only future traffic uses new factor`
          : `New quota: ${JSON.parse(job.Value).limit_gb} metered GB · factor ${JSON.parse(job.Value).weight}`;
      return {id:peer.id, name:peer.name || peer.id, ok:true, result, restricted:!!peer.restricted};
    } catch(e) {
      return {id:peer.id, name:peer.name || peer.id, ok:false,
        result:e?.message || "Invalid policy", restricted:!!peer.restricted};
    }
  });
});
const restrictedRenewals = computed(() =>
  (action.value === "time" || action.value === "traffic" || action.value === "factor") &&
  previewPeers.value.some(p => p.restricted));
const readyToExecute = computed(() => {
  if (!action.value || !confirmIds.value.length || busy.value) return false;
  if (action.value === "delete") return confirmation.value === `DELETE ${confirmIds.value.length}`;
  if (action.value === "time" || action.value === "traffic" || action.value === "factor") {
    if ((action.value !== "factor" && !validTimeZone(serverTimeZone.value)) ||
      (action.value === "factor" && (!factorConsent.value || !Number.isFinite(Number(factorValue.value)) ||
        Number(factorValue.value) < 0.1 || Number(factorValue.value) > 10)) ||
      renewalPreview.value.length !== confirmIds.value.length ||
      renewalPreview.value.some(p => !p.ok) ||
      (restrictedRenewals.value && !temporaryAccessConsent.value) ||
      (restrictedRenewals.value && confirmIds.value.length > 20)) return false;
    return true;
  }
  if (action.value.startsWith("tag_")) return !!chosenTag.value;
  return true;
});
const callPost = async (endpoint, data) => {
  let response = null;
  await fetchPost(endpoint, data, res => { response = res; });
  return response || {status:false, message:"Request failed or the session has ended."};
};
const callGet = async () => {
  let response = null;
  await fetchGet("/api/getWireguardConfigurationInfo",
    {configurationName: props.configuration.Name}, res => { response = res; });
  return response;
};
const classifyChanges = (initial, current, kind) => {
  // Concurrent edits to time/traffic policies must not be silently overwritten.
  if (kind !== "time" && kind !== "traffic" && kind !== "factor") return true;
  const a = peerPolicy(initial), b = peerPolicy(current);
  const key = kind === "time" ? "timeJob" : "quotaJob";
  return JSON.stringify(a[key] || null) === JSON.stringify(b[key] || null);
};
const executeAction = async () => {
  if (!readyToExecute.value) return;
  busy.value = true;
  error.value = "";
  results.value = [];
  progress.value = "Checking current peer state...";
  try {
    // Freeze identities and revalidate against an independent CURRENT read
    // before any write. Never use unchecked browser-computed filtered IDs.
    const fresh = await callGet();
    if (!fresh?.status || !Array.isArray(fresh.data?.configurationPeers) ||
      !Array.isArray(fresh.data?.configurationRestrictedPeers)) {
      throw new Error(fresh?.message || "Could not verify the selected peers.");
    }
    const current = [
      ...fresh.data.configurationPeers.map(p => ({...p, restricted: false})),
      ...fresh.data.configurationRestrictedPeers.map(p => ({...p, restricted: true}))
    ];
    const live = new Map(current.map(p => [p.id, p]));
    const stale = confirmIds.value.filter(id => !live.has(id));
    if (stale.length) throw new Error("Some selected peers no longer exist. Reload and select again.");

    const successes = [];
    const failures = [];
    const report = (ids, success, message) => {
      for (const id of ids) {
        (success ? successes : failures).push({id, message: message || (success ? "Done" : "Failed")});
      }
      results.value = [...successes.map(r => ({...r, success: true})),
        ...failures.map(r => ({...r, success: false}))];
    };
    const scope = `/api/`;
    const ids = [...confirmIds.value];
    if (action.value === "restrict" || action.value === "unrestrict" || action.value === "delete") {
      const operation = {restrict:"restrictPeers", unrestrict:"allowAccessPeers", delete:"deletePeers"}[action.value];
      // Existing endpoints accept arrays. Small batches reduce VPN disruption
      // and avoid triggering a huge database or WireGuard transaction.
      for (let n = 0; n < ids.length; n += 20) {
        const part = ids.slice(n, n+20);
        progress.value = `Applying ${actionLabel.value}: ${Math.min(n+20, ids.length)}/${ids.length}`;
        const response = await callPost(`${scope}${operation}/${props.configuration.Name}`, {peers: part});
        report(part, !!response.status, response.message);
        if (!response.status) break; // Don't amplify a systemic failure.
      }
    } else if (action.value === "tag_add" || action.value === "tag_remove") {
      const currentGroups = structuredClone(fresh.data.configurationInfo?.Info?.PeerGroups || {});
      const target = currentGroups[chosenTag.value];
      if (!target || !Array.isArray(target.Peers)) throw new Error("Selected tag no longer exists.");
      const set = new Set(target.Peers);
      for (const id of ids) action.value === "tag_add" ? set.add(id) : set.delete(id);
      target.Peers = [...set];
      progress.value = "Updating selected tag...";
      const response = await callPost("/api/updateWireguardConfigurationInfo", {
        Name:props.configuration.Name, Key:"PeerGroups", Value:currentGroups
      });
      report(ids, !!response.status, response.message);
    } else if (action.value === "time" || action.value === "traffic" || action.value === "factor") {
      const original = new Map(records.value.map(p => [p.id, p]));
      const value = Number(amount.value);
      for (let i=0; i<ids.length; i++) {
        const id = ids[i], peer = live.get(id);
        progress.value = `Updating ${i+1}/${ids.length}`;
        if (!classifyChanges(original.get(id), peer, action.value) ||
          !!original.get(id)?.restricted !== !!peer.restricted) {
          report([id], false, "Limits or access changed since selection; skipped");
          continue;
        }
        let saved = false, failure = "", restored = true;
        try {
          const job = action.value === "factor"
            ? plannedFactorJob(peer, props.configuration.Name, factorValue.value)
            : plannedJob(peer, props.configuration.Name, action.value, value,
                new Date(), uuid, {
                  serverTimeZone:serverTimeZone.value,
                  assumeWeightOne:assumeWeightOne.value
                });
          if (peer.restricted) {
            const outcome = await renewRestrictedWithRestore({
              id, configuration:props.configuration.Name, job,
              consent:temporaryAccessConsent.value, post:callPost, get:callGet,
              onRestoring:() => { progress.value = `Restoring restriction for ${i+1}/${ids.length}`; }
            });
            saved = outcome.saved;
            restored = outcome.restored;
            failure = outcome.error;
          } else {
            const response = await callPost("/api/savePeerScheduleJob", {Job:job});
            saved = !!response.status;
            if (!saved) failure = response.message || "Could not save policy job";
          }
        } catch (e) {
          failure = e?.message || "Unable to renew this peer";
        }
        if (!restored) {
          error.value = `CRITICAL: Restricted peer ${id} may have access. Verify access and re-restrict immediately.`;
        }
        report([id], saved && restored, failure || (peer.restricted
          ? "Policy saved; access remains restricted" : "Policy saved"));
        if (!restored) break; // Never continue after an uncertain access state.
      }
    }
    progress.value = `Completed: ${successes.length} successful, ${failures.length} failed.`;
    const done = new Set(successes.map(x => x.id));
    selected.value = selected.value.filter(id => !done.has(id));
    emit("refresh");
    action.value = "";
    confirmation.value = "";
    confirmIds.value = [];
    // refresh() is blocked while busy, so load after clearing the flag.
  } catch(e) {
    error.value = e?.message || "Bulk action did not complete.";
  } finally {
    busy.value = false;
    const operationError = error.value;
    await refresh();
    if (operationError) error.value = operationError;
  }
};
</script>

<template>
  <div class="peerSettingContainer w-100 h-100 position-absolute top-0 start-0 overflow-y-auto" role="dialog" aria-modal="true" aria-label="Bulk peer manager">
    <div class="container py-3 h-100 d-flex">
      <div class="card rounded-3 shadow m-auto w-100" style="max-width: 1050px; max-height: 92vh;">
        <div class="card-header bg-transparent d-flex align-items-center gap-2 p-3">
          <h5 class="mb-0">Bulk peer manager</h5>
          <small class="text-muted ms-2">{{records.length}} peers · {{selected.length}} selected</small>
          <button type="button" class="btn-close ms-auto" :disabled="busy" @click="emit('close')"></button>
        </div>
        <div class="card-body overflow-auto d-flex flex-column gap-3">
          <div v-if="error" class="alert alert-warning mb-0" role="alert">{{error}}</div>
          <div v-if="progress" class="alert alert-secondary py-2 mb-0" role="status">{{progress}}</div>
          <p class="text-muted small mb-0">
            Uses existing APIs only. "Unlimited" means no active limit on an allowed peer.
            Restricted peers with unknown historical limits show "unknown", not unlimited.
            Completed limit jobs are not present in the old API; expired reasons may be unidentifiable.
          </p>
          <div class="d-flex gap-2 flex-wrap">
            <label class="flex-grow-1" style="min-width: 190px">
              <span class="small text-muted">Search</span>
              <input class="form-control form-control-sm" v-model="query" placeholder="Name, public key, IP" :disabled="busy || !!action">
            </label>
            <label class="flex-grow-1" style="min-width: 175px">
              <span class="small text-muted">Status</span>
              <select class="form-select form-select-sm" v-model="statusFilter" :disabled="busy || !!action">
                <option value="all">All peers</option>
                <option value="expired">Expired</option>
                <option value="expiring_soon">Expiring soon</option>
                <option value="unlimited">Unlimited</option>
                <option value="unlimited_time">No time limit</option>
                <option value="unlimited_traffic">No data limit</option>
                <option value="quota_limited">Has data quota</option>
                <option value="restricted">Restricted</option>
                <option value="active">Active</option>
                <option value="unknown">Needs review</option>
              </select>
            </label>
            <button type="button" class="btn btn-sm btn-outline-secondary align-self-end"
              :disabled="busy || !!action || loading" @click="refresh">Refresh</button>
          </div>

          <div v-if="!validTimeZone(serverTimeZone)" class="alert alert-warning mb-0 py-2 small">
            Invalid server timezone. Use the container's WGD_TZ setting, e.g. Europe/Istanbul.
          </div>
          <div class="d-flex gap-3 flex-wrap align-items-center small">
            <label class="d-flex align-items-center gap-2">
              <input type="checkbox" :checked="selectedOnPage"
                :disabled="busy || !!action || !visible.length"
                @change="togglePage($event.target.checked)">
              Select page ({{visible.length}})
            </label>
            <label class="d-flex align-items-center gap-2">
              <input type="checkbox" :checked="selectedFiltered"
                :disabled="busy || !!action || !filtered.length"
                @change="toggleFiltered($event.target.checked)">
              Select all {{filtered.length}} matching peers
            </label>
            <button type="button" class="btn btn-link btn-sm p-0" :disabled="busy || !!action"
              @click="selected = []">Clear selection</button>
            <span class="ms-auto text-muted" v-if="loading">Loading full peer list…</span>
            <span class="ms-auto text-muted" v-else>Showing {{visible.length}} / {{filtered.length}} matches</span>
          </div>

          <div class="table-responsive border rounded-3 bulk-table-scroll" style="max-height: 37vh; overflow-y: auto;">
            <table class="table table-sm table-hover align-middle mb-0 bulk-table" aria-label="Bulk peer selection">
              <thead class="sticky-top">
                <tr>
                  <th scope="col" class="bulk-check-col"></th>
                  <th scope="col" :aria-sort="ariaSort('name')">
                    <button type="button" class="bulk-sort" :disabled="busy || !!action"
                      @click="toggleSort('name')">User
                      <span class="bulk-sort-icon" aria-hidden="true">{{sortSymbol('name')}}</span>
                    </button>
                  </th>
                  <th scope="col" :aria-sort="ariaSort('status')">
                    <button type="button" class="bulk-sort" :disabled="busy || !!action"
                      @click="toggleSort('status')">Status
                      <span class="bulk-sort-icon" aria-hidden="true">{{sortSymbol('status')}}</span>
                    </button>
                  </th>
                  <th scope="col" :aria-sort="ariaSort('time_remaining')">
                    <button type="button" class="bulk-sort" :disabled="busy || !!action"
                      @click="toggleSort('time_remaining')">Time
                      <span class="bulk-sort-icon" aria-hidden="true">{{sortSymbol('time_remaining')}}</span>
                    </button>
                  </th>
                  <th scope="col" :aria-sort="ariaSort('usage')">
                    <button type="button" class="bulk-sort" :disabled="busy || !!action"
                      @click="toggleSort('usage')">Data used (GB)
                      <span class="bulk-sort-icon" aria-hidden="true">{{sortSymbol('usage')}}</span>
                    </button>
                  </th>
                  <th scope="col" :aria-sort="ariaSort('access')">
                    <button type="button" class="bulk-sort" :disabled="busy || !!action"
                      @click="toggleSort('access')">Access
                      <span class="bulk-sort-icon" aria-hidden="true">{{sortSymbol('access')}}</span>
                    </button>
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in visible" :key="item.peer.id">
                  <td>
                    <input type="checkbox" :checked="selected.includes(item.peer.id)"
                      :disabled="busy || !!action"
                      :aria-label="'Select '+(item.peer.name || item.peer.id)"
                      @change="toggle(item.peer.id, $event.target.checked)">
                  </td>
                  <td>
                    <div class="fw-semibold bulk-user-name">{{item.peer.name || 'Untitled Peer'}}</div>
                    <small class="text-muted d-block bulk-user-detail"
                      :title="'Peer: '+item.peer.id+' · Last handshake: '+(item.peer.latest_handshake || 'N/A')">
                      {{item.peer.allowed_ip || item.peer.id.slice(0, 18) + '…'}}
                    </small>
                  </td>
                  <td>
                    <span class="bulk-status" :class="'bulk-status--'+item.summary.key"
                      :title="item.summary.key === 'review' ? 'The old API cannot reveal the reason for a completed restriction; review manually.' : item.summary.label">
                      {{item.summary.label}}
                    </span>
                  </td>
                  <td>
                    <small class="bulk-time" :title="item.policy.deadline !== null
                      ? 'Expiry (server time): '+new Date(item.policy.deadline).toISOString().slice(0,19).replace('T',' ')+' · '+serverTimeZone
                      : 'No reliable active time limit date'">
                      {{item.timeLabel}}
                    </small>
                  </td>
                  <td>
                    <div class="bulk-data-bar" :class="'bulk-data-bar--'+item.dataBar.state"
                      :role="item.dataBar.hasQuota ? 'progressbar' : 'img'"
                      :aria-valuemin="item.dataBar.hasQuota ? 0 : undefined"
                      :aria-valuemax="item.dataBar.hasQuota ? item.dataBar.quota : undefined"
                      :aria-valuenow="item.dataBar.hasQuota ? Math.min(item.dataBar.used, item.dataBar.quota) : undefined"
                      :aria-label="item.dataBar.details" :title="item.dataBar.details">
                      <div class="bulk-data-bar__segments" aria-hidden="true">
                        <span class="bulk-data-bar__download" :style="{width:item.dataBar.downloadPct+'%'}"></span>
                        <span class="bulk-data-bar__upload" :style="{width:item.dataBar.uploadPct+'%'}"></span>
                        <span class="bulk-data-bar__other" :style="{width:item.dataBar.otherPct+'%'}"></span>
                      </div>
                      <span class="bulk-data-bar__label" aria-hidden="true">{{item.dataBar.label}}</span>
                    </div>
                  </td>
                  <td><small :class="item.peer.restricted ? 'text-danger-emphasis' : 'text-success-emphasis'">
                    {{item.peer.restricted ? 'Restricted' : 'Allowed'}}
                  </small></td>
                </tr>
                <tr v-if="!visible.length"><td colspan="6" class="text-center text-muted py-3">No matching peers.</td></tr>
              </tbody>
            </table>
          </div>
          <div class="d-flex gap-2 align-items-center justify-content-between">
            <small class="text-muted">{{selected.length}} selected · Page {{page}} of {{pageCount}}</small>
            <div class="btn-group">
              <button class="btn btn-sm btn-outline-secondary" :disabled="busy || !!action || page<=1" @click="page--">Previous</button>
              <button class="btn btn-sm btn-outline-secondary" :disabled="busy || !!action || page>=pageCount" @click="page++">Next</button>
            </div>
          </div>

          <div class="border-top pt-3 d-flex flex-wrap gap-2 align-items-center">
            <strong class="small me-2">{{selected.length}} selected</strong>
            <button type="button" class="btn btn-sm btn-outline-warning" :disabled="!selected.length || busy || !!action" @click="openAction('restrict')">Restrict</button>
            <button type="button" class="btn btn-sm btn-outline-success" :disabled="!selected.length || busy || !!action" @click="openAction('unrestrict')">Allow access</button>
            <button type="button" class="btn btn-sm btn-outline-primary" :disabled="!selected.length || busy || !!action" @click="openAction('time')">Extend time</button>
            <button type="button" class="btn btn-sm btn-outline-primary" :disabled="!selected.length || busy || !!action" @click="openAction('traffic')">Add traffic</button>
            <button type="button" class="btn btn-sm btn-outline-primary" :disabled="!selected.length || busy || !!action" @click="openAction('factor')">Set factor</button>
            <button type="button" class="btn btn-sm btn-outline-secondary" :disabled="!selected.length || busy || !!action || !tagOptions.length" @click="openAction('tag_add')">Add tag</button>
            <button type="button" class="btn btn-sm btn-outline-secondary" :disabled="!selected.length || busy || !!action || !tagOptions.length" @click="openAction('tag_remove')">Remove tag</button>
            <button type="button" class="btn btn-sm btn-outline-danger ms-auto" :disabled="!selected.length || busy || !!action" @click="openAction('delete')">Delete…</button>
          </div>

          <div v-if="action" class="border rounded-3 p-3 d-flex flex-column gap-3">
            <h6 class="mb-0">{{actionLabel}} — {{confirmIds.length}} peers</h6>
            <p class="small mb-0">
              Review the selected peers; no API call writes anything before Confirm.
              Jobs are not transactional. Expired history is not exposed by the existing API,
              so a restricted peer's cause cannot be verified. Renewals never automatically
              lift an existing restriction.
            </p>
            <div class="small border rounded-2 p-2" style="max-height:100px;overflow:auto">
              <div v-for="peer in previewPeers" :key="peer.id">
                {{peer.name || 'Untitled'}} <span class="text-muted font-monospace">({{peer.id.slice(0, 14)}}…)</span>
              </div>
            </div>
            <label v-if="action === 'time' || action === 'traffic'" class="small">
              {{action === 'time' ? 'Add days (from now or current future expiry)' : 'Add GB (to existing quota, or current usage + GB)'}}
              <input class="form-control form-control-sm" type="number" min="1"
                :max="action === 'time' ? 3650 : 1000000" :step="action === 'time' ? 1 : 0.1"
                v-model.number="amount" :disabled="busy">
            </label>
            <template v-if="action === 'time' || action === 'traffic' || action === 'factor'">
              <div v-if="action === 'factor'" class="alert alert-warning small mb-0">
                Changes the billing multiplier for EXISTING peers with supported active quota jobs.
                Their existing GB quota and PAST billed usage stay unchanged.
                Only FUTURE traffic uses the new factor.
                Unlimited/no-quota peers cannot be changed individually by this old API.
                Nothing is updated until Confirm.
              </div>
              <label v-if="action === 'factor'" class="small">
                New traffic factor (0.1 – 10, from Peers Settings)
                <input class="form-control form-control-sm" type="number" min="0.1"
                  max="10" step="0.1" v-model.number="factorValue" :disabled="busy">
              </label>
              <label v-if="action === 'factor'" class="small d-flex gap-2 align-items-start">
                <input type="checkbox" v-model="factorConsent" :disabled="busy">
                I understand that past billed usage stays fixed and future traffic uses the new factor.
              </label>
              <label v-if="action !== 'factor'" class="small">
                Server timezone (match the deployed WGD_TZ, not your browser timezone)
                <input class="form-control form-control-sm" v-model.trim="serverTimeZone"
                  :disabled="busy" spellcheck="false" placeholder="Europe/Istanbul">
              </label>
              <label v-if="action === 'traffic'" class="small d-flex align-items-start gap-2">
                <input type="checkbox" v-model="assumeWeightOne" :disabled="busy">
                If a peer has zero usage and no active quota, use factor 1. The historical
                weight cannot be recovered from the existing API in that case.
              </label>
              <div class="small border rounded p-2" style="max-height:180px;overflow-y:auto">
                <strong>Before / after preview ({{renewalPreview.length}} peers)</strong>
                <div v-for="item in renewalPreview" :key="item.id"
                  :class="item.ok ? 'text-body' : 'text-danger'">
                  <strong>{{item.name}}</strong> — {{item.result}}
                  <span v-if="item.restricted" class="text-warning-emphasis">
                    · RESTRICTED: remains restricted after renewal
                  </span>
                </div>
              </div>
              <div v-if="restrictedRenewals" class="alert alert-warning mb-0 small" role="alert">
                <strong>Restricted users: extra security step.</strong>
                The unchanged API will not accept a new limit for restricted peers.
                Continuing requires temporarily allowing each restricted peer,
                saving the limit, and re-restricting it (with verification).
                Access can be briefly possible during this process; the operation
                stops if restriction cannot be verified. Limit restricted renewals
                to 20 selected peers per operation. For zero exposure, cancel and
                renew individually using your own maintenance procedure.
                <label class="d-flex gap-2 mt-2">
                  <input type="checkbox" v-model="temporaryAccessConsent" :disabled="busy">
                  I explicitly accept temporary access and mandatory re-restriction.
                </label>
              </div>
            </template>
            <label v-if="action.startsWith('tag_')" class="small">
              Tag
              <select class="form-select form-select-sm" v-model="chosenTag" :disabled="busy">
                <option v-for="tag in tagOptions" :key="tag.id" :value="tag.id">{{tag.name}}</option>
              </select>
            </label>
            <label v-if="action === 'delete'" class="small">
              Irreversible: type <strong>DELETE {{confirmIds.length}}</strong> to delete these peers.
              <input class="form-control form-control-sm mt-1" v-model="confirmation" :disabled="busy" autocomplete="off">
            </label>
            <div class="d-flex gap-2 justify-content-end">
              <button class="btn btn-sm btn-outline-secondary" :disabled="busy" @click="cancelAction">Cancel</button>
              <button class="btn btn-sm" :class="action === 'delete' ? 'btn-danger' : 'btn-primary'"
                :disabled="!readyToExecute" @click="executeAction">
                {{busy ? 'Applying…' : 'Confirm ' + actionLabel}}
              </button>
            </div>
          </div>

          <div v-if="results.length" class="small border rounded-3 p-2">
            <strong>Last operation result</strong>
            <div class="text-muted">Success: {{results.filter(r => r.success).length}} · Failed: {{results.filter(r => !r.success).length}}</div>
            <div v-for="r in results.slice(0, 40)" :key="r.id" :class="r.success ? 'text-success' : 'text-danger'">
              {{r.id.slice(0, 16)}}… — {{r.success ? 'OK' : (r.message || 'Failed')}}
            </div>
            <div v-if="results.length > 40" class="text-muted">{{results.length-40}} further results omitted from preview.</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* Table stays usable at desktop widths; narrow screens scroll horizontally. */
.bulk-table { min-width: 790px; }
.bulk-table thead th {
  background: var(--bs-body-bg);
  color: var(--bs-body-color);
  z-index: 1;
  vertical-align: middle;
}
.bulk-check-col { width: 38px; }
.bulk-sort {
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  gap: .5rem;
  padding: .3rem .15rem;
  border: 0;
  border-radius: .25rem;
  background: transparent;
  color: inherit;
  width: 100%;
  text-align: start;
  font: inherit;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
}
.bulk-sort:hover:not(:disabled) { color: var(--bs-primary); }
.bulk-sort:focus-visible { outline: 2px solid var(--bs-primary); outline-offset: 2px; }
.bulk-sort:disabled { cursor: default; opacity: .65; }
.bulk-sort-icon { font-size: .7rem; opacity: .72; min-width: 1em; }
.bulk-user-name { max-width: 180px; overflow-wrap: anywhere; }
.bulk-user-detail { font-size: .74rem; max-width: 180px; overflow: hidden; text-overflow: ellipsis; }
.bulk-time { font-variant-numeric: tabular-nums; white-space: nowrap; }
.bulk-status {
  display: inline-block;
  padding: .23rem .48rem;
  border-radius: .4rem;
  font-size: .73rem;
  font-weight: 700;
  white-space: nowrap;
  border: 1px solid transparent;
}
.bulk-status--time { color: #78350f; background: #fef3c7; border-color: #f59e0b; }
.bulk-status--data { color: #1e40af; background: #dbeafe; border-color: #93c5fd; }
.bulk-status--both { color: #9f1239; background: #ffe4e6; border-color: #fda4af; }
.bulk-status--review { color: #334155; background: #e2e8f0; border-color: #94a3b8; }
.bulk-status--restricted { color: #7c2d12; background: #ffedd5; border-color: #fdba74; }
.bulk-status--soon { color: #713f12; background: #fef9c3; border-color: #eab308; }
.bulk-status--active { color: #166534; background: #dcfce7; border-color: #86efac; }

/* ONE stacked download/upload progress graphic with the only visible
   number inside it. A separate opaque label keeps text readable over either
   bar colour, including when usage reaches 100%. Theme vars adapt dark mode. */
.bulk-data-bar {
  width: 154px;
  min-width: 130px;
  height: 27px;
  border: 1px solid var(--bs-border-color, #94a3b8);
  border-radius: .45rem;
  position: relative;
  overflow: hidden;
  background: var(--bs-tertiary-bg, #e2e8f0);
  margin-block: .2rem;
}
.bulk-data-bar__segments {
  position: absolute;
  inset: 0;
  display: flex;
  overflow: hidden;
}
.bulk-data-bar__segments span { display: block; height: 100%; flex: 0 0 auto; }
.bulk-data-bar__download { background: #2563eb; }
.bulk-data-bar__upload { background: #0d9488; }
.bulk-data-bar__other { background: #6366f1; }
.bulk-data-bar__label {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  padding: .05rem .42rem;
  min-width: 63px;
  text-align: center;
  border-radius: .33rem;
  border: 1px solid var(--bs-border-color, #94a3b8);
  background: var(--bs-body-bg, #fff);
  color: var(--bs-body-color, #0f172a);
  box-shadow: 0 0 0 1px rgba(0,0,0,.08);
  font-size: .73rem;
  font-family: var(--bs-font-monospace, monospace);
  font-weight: 750;
  line-height: 1.35;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.bulk-data-bar--warning { border-color: #d97706; border-width: 2px; }
.bulk-data-bar--exhausted { border-color: #be123c; border-width: 2px; }
.bulk-data-bar--unknown { border-style: dashed; }
:global([data-bs-theme="dark"]) .bulk-data-bar__download { background: #60a5fa; }
:global([data-bs-theme="dark"]) .bulk-data-bar__upload { background: #2dd4bf; }
:global([data-bs-theme="dark"]) .bulk-data-bar__other { background: #a5b4fc; }
</style>
