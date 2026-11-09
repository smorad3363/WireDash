<script setup>
import {computed, nextTick, onMounted, ref, useId} from 'vue'
import {getUrl} from '@/utilities/fetch.js'
import {DashboardConfigurationStore} from '@/stores/DashboardConfigurationStore.js'

const props = defineProps({peerId: String, configuration: String})
const emit = defineEmits(['saved'])
const id = useId()
const store = DashboardConfigurationStore()
const model = ref(null), form = ref(null), csrf = ref(''), error = ref(''), notice = ref('')
const busy = ref(false), action = ref(''), confirm = ref(false), overwrite = ref(false)
const dialog = ref(null), returnFocus = ref(null), panel = ref(null)
const mode = ref('inherit'), master = ref(false), preview = ref(null), baseline = ref('')
const rememberedRates = ref({download: 1, upload: 1})
const directions = [
  {key: 'download', label: 'Download', icon: 'bi-arrow-down'},
  {key: 'upload', label: 'Upload', icon: 'bi-arrow-up'}
]
const fallbackPolicy = () => ({enabled: false, period_days: 30, download_gb: 30, upload_gb: 30, download_mbps: null, upload_mbps: null})
const signature = () => JSON.stringify({policy: form.value, mode: props.peerId ? mode.value : null, master: props.peerId ? null : master.value})
const hasChanges = computed(() => !!model.value && signature() !== baseline.value)
const editable = computed(() => !props.peerId || mode.value === 'custom')
const needsRate = computed(() => editable.value && form.value?.enabled && directions.every(d => form.value[d.key + '_mbps'] === null))
const canSave = computed(() => hasChanges.value && !busy.value && !needsRate.value)
const serviceStatus = computed(() => ({inactive: 'Monitoring', applied: 'Limits applied', error: 'Needs attention', interface_stopped: 'Interface stopped'}[model.value?.controller?.status] || 'Waiting for a sample'))
const date = seconds => seconds ? new Intl.DateTimeFormat(undefined, {dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Tehran'}).format(new Date(seconds * 1000)) : 'Not recorded'
const gb = value => Number(value || 0).toLocaleString(undefined, {maximumFractionDigits: 2})
const rate = direction => form.value?.[direction + '_mbps'] === null ? 'Unlimited' : `${gb(form.value?.[direction + '_mbps'])} Mbps`
const endpoint = () => props.peerId ? `/api/ui/speed/peer/${encodeURIComponent(props.configuration)}` : '/api/ui/speed/settings'
const readEndpoint = () => endpoint() + (props.peerId ? '?id=' + encodeURIComponent(props.peerId) : '')

async function request(path, body) {
  if (store.getActiveCrossServer()) throw new Error('Sign in directly to this server to manage speed policies.')
  const response = await fetch(getUrl(path), {
    credentials: 'same-origin', method: body ? 'POST' : 'GET',
    headers: body ? {'Content-Type': 'application/json', 'X-Speed-CSRF': csrf.value} : {},
    body: body ? JSON.stringify(body) : undefined
  })
  const result = await response.json()
  if (!response.ok || !result.status) throw new Error(result.message || 'Request failed')
  csrf.value = result.csrf || csrf.value
  return result.data
}
function applyLoaded(data) {
  model.value = data
  form.value = structuredClone(data.policy || fallbackPolicy())
  mode.value = data.mode || 'inherit'
  master.value = data.master_enabled || false
  for (const direction of directions) rememberedRates.value[direction.key] = form.value[direction.key + '_mbps'] ?? 1
  baseline.value = signature()
}
async function load() {
  busy.value = true; action.value = 'load'; error.value = ''; notice.value = ''
  try { applyLoaded(await request(readEndpoint())) }
  catch (e) { error.value = e.message }
  finally { busy.value = false; action.value = '' }
}
function setUnlimited(direction, unlimited) {
  const key = direction + '_mbps'
  if (unlimited) {
    if (form.value[key] !== null) rememberedRates.value[direction] = form.value[key]
    form.value[key] = null
  } else {
    form.value[key] = rememberedRates.value[direction]
  }
}
function closeConfirm() {
  dialog.value?.close()
  confirm.value = false
}
async function restoreFocus() {
  await nextTick()
  if (returnFocus.value?.isConnected && !returnFocus.value.disabled) returnFocus.value.focus()
  else panel.value?.focus()
}
async function dismissConfirm() {
  if (busy.value) return
  closeConfirm()
  await restoreFocus()
}
async function review() {
  if (!canSave.value) return
  busy.value = true; action.value = 'review'; error.value = ''; notice.value = ''
  returnFocus.value = document.activeElement
  try {
    preview.value = await request('/api/ui/speed/settings')
    overwrite.value = false
    busy.value = false; action.value = ''
    confirm.value = true
    await nextTick()
    dialog.value.showModal()
  } catch (e) { error.value = e.message }
  finally { busy.value = false; action.value = '' }
}
async function save() {
  if (busy.value || !hasChanges.value || needsRate.value) return
  busy.value = true; action.value = 'save'; error.value = ''; notice.value = ''
  try {
    const body = props.peerId
      ? {id: props.peerId, mode: mode.value, policy: form.value, revision: model.value.revision}
      : {policy: form.value, revision: preview.value.revision, overwrite: overwrite.value, master_enabled: master.value}
    await request(endpoint(), body)
    closeConfirm()
    applyLoaded(await request(readEndpoint()))
    notice.value = props.peerId ? 'Account policy saved.' : 'Speed settings saved.'
    emit('saved')
  } catch (e) { error.value = e.message; closeConfirm() }
  finally { busy.value = false; action.value = ''; if (!props.peerId) await restoreFocus() }
}
onMounted(load)
</script>

<template>
  <section ref="panel" class="card speed-editor" tabindex="-1" :aria-label="peerId ? 'Account speed policy' : 'Speed limits'">
    <header class="speed-editor__header">
      <div class="speed-editor__heading">
        <span class="speed-editor__icon"><i class="bi bi-speedometer2" aria-hidden="true"></i></span>
        <div><h3>{{peerId ? 'Account speed policy' : 'Speed limits'}}</h3><p>Reduce speed after a usage threshold in each period.</p></div>
      </div>
      <span v-if="model && !peerId" class="speed-status" :class="{'is-on': model.master_enabled}">
        <i :class="model.master_enabled ? 'bi bi-check-circle' : 'bi bi-pause-circle'" aria-hidden="true"></i>
        Enforcement {{model.master_enabled ? 'on' : 'off'}}
      </span>
    </header>

    <div v-if="error" class="speed-feedback alert alert-danger" role="alert"><i class="bi bi-exclamation-circle me-2" aria-hidden="true"></i>{{error}}<button v-if="!model" class="btn btn-sm btn-outline-danger ms-2" :disabled="busy" @click="load">Try again</button></div>
    <div v-if="!model && !error" class="speed-loading" role="status"><span class="spinner-border spinner-border-sm" aria-hidden="true"></span> Loading speed settings…</div>

    <form v-if="model && form" @submit.prevent="peerId ? save() : review()">
      <fieldset class="speed-editor__form" :disabled="busy">
        <div class="speed-controls">
          <label v-if="!peerId" class="speed-control form-switch">
            <span><strong>Speed enforcement</strong><small>Master control for global and custom rules.</small></span>
            <input class="form-check-input speed-switch" type="checkbox" role="switch" v-model="master" aria-label="Speed enforcement">
          </label>
          <label v-else class="speed-mode" :for="id + '-mode'"><span>Account policy</span>
            <select :id="id + '-mode'" v-model="mode" class="form-select">
              <option value="inherit">Follow global settings</option><option value="custom">Custom settings</option><option value="exempt">Exempt from speed reduction</option>
            </select>
          </label>
          <label class="speed-control form-switch" :class="{'is-disabled': !editable}">
            <span><strong>{{peerId ? 'Custom rule' : 'Default rule'}}</strong><small>{{peerId ? 'Apply these thresholds to this account.' : 'Used by accounts following global settings.'}}</small></span>
            <input class="form-check-input speed-switch" type="checkbox" role="switch" v-model="form.enabled" :disabled="!editable" :aria-label="peerId ? 'Custom rule' : 'Default rule'">
          </label>
        </div>
        <p v-if="!peerId && !master" class="speed-context"><i class="bi bi-pause-circle" aria-hidden="true"></i>Enforcement is paused. Saved rules stay available.</p>
        <p v-else-if="!peerId && !form.enabled" class="speed-context"><i class="bi bi-info-circle" aria-hidden="true"></i>The default rule is off. Enabled custom rules can still apply.</p>
        <p v-if="peerId && !editable" class="speed-context"><i class="bi bi-info-circle" aria-hidden="true"></i>{{mode === 'exempt' ? 'This account is exempt from speed reduction. Paid quotas and expiry remain active.' : 'Showing the effective global rule. Select Custom settings to edit this account.'}}</p>

        <fieldset class="speed-rule-fields" :disabled="!editable">
          <div class="speed-period">
            <div><label :for="id + '-period'">Usage period</label><p>Starts on creation or a full usage reset.</p></div>
            <div class="input-group speed-period__input"><input :id="id + '-period'" class="form-control" type="number" min="1" max="366" step="1" required v-model.number="form.period_days"><span class="input-group-text">days</span></div>
          </div>
          <div class="speed-rule-grid">
            <section v-for="direction in directions" :key="direction.key" class="speed-direction" :aria-label="direction.label + ' limits'">
              <header class="speed-direction__header"><span class="speed-direction__icon"><i :class="'bi ' + direction.icon" aria-hidden="true"></i></span><div><h4>{{direction.label}}</h4><p>Client {{direction.label.toLowerCase()}}</p></div><span class="speed-direction__rate">{{rate(direction.key)}}</span></header>
              <div class="speed-segment" role="group" :aria-label="direction.label + ' speed mode'">
                <input class="btn-check" type="radio" :id="id + '-' + direction.key + '-limited'" :name="id + '-' + direction.key + '-mode'" :checked="form[direction.key + '_mbps'] !== null" @change="setUnlimited(direction.key, false)">
                <label :for="id + '-' + direction.key + '-limited'">Limit speed</label>
                <input class="btn-check" type="radio" :id="id + '-' + direction.key + '-unlimited'" :name="id + '-' + direction.key + '-mode'" :checked="form[direction.key + '_mbps'] === null" @change="setUnlimited(direction.key, true)">
                <label :for="id + '-' + direction.key + '-unlimited'">Unlimited</label>
              </div>
              <div class="speed-direction__fields">
                <div><label class="speed-label" :for="id + '-' + direction.key + '-threshold'">Usage threshold</label><div class="input-group"><input class="form-control" :id="id + '-' + direction.key + '-threshold'" :aria-label="direction.label + ' threshold'" type="number" min="0.000001" max="1000000" step="any" required v-model.number="form[direction.key + '_gb']"><span class="input-group-text">GB</span></div></div>
                <div><label class="speed-label" :for="id + '-' + direction.key + '-rate'">Speed after threshold</label><div class="input-group"><input class="form-control" :id="id + '-' + direction.key + '-rate'" :aria-label="direction.label + ' speed'" type="number" min="0.001" max="100000" step="any" required v-model.number="form[direction.key + '_mbps']" :disabled="form[direction.key + '_mbps'] === null" :placeholder="form[direction.key + '_mbps'] === null ? 'Unlimited' : ''"><span class="input-group-text">Mbps</span></div></div>
              </div>
            </section>
          </div>
        </fieldset>
        <p v-if="needsRate" class="speed-validation" role="alert">Choose a reduced speed in at least one direction to enable this rule.</p>
        <details class="speed-help"><summary><i class="bi bi-info-circle" aria-hidden="true"></i>How periods and resets work</summary><ul><li>Download and upload thresholds apply independently.</li><li>A full usage reset starts a fresh period and removes triggered speed limits. Server restarts preserve usage.</li><li>Period length changes take effect at the next boundary. Saving a rule keeps current consumption.</li><li>Account expiry and paid traffic quotas remain active.</li></ul></details>
      </fieldset>
      <footer class="speed-editor__footer">
        <span class="speed-save-state" role="status" :class="{'is-dirty': hasChanges, 'is-saved': !!notice}"><i :class="notice ? 'bi bi-check-circle' : hasChanges ? 'bi bi-circle-fill' : 'bi bi-shield-check'" aria-hidden="true"></i>{{notice || (hasChanges ? 'Unsaved changes' : 'Saved settings')}}</span>
        <div class="speed-actions"><button class="btn btn-outline-secondary" type="button" :disabled="busy" @click="load"><i class="bi bi-arrow-clockwise" aria-hidden="true"></i>Reload</button><button class="btn btn-primary" type="submit" :disabled="!canSave"><span v-if="busy && action !== 'load'" class="spinner-border spinner-border-sm" aria-hidden="true"></span><i v-else class="bi" :class="peerId ? 'bi-check2' : 'bi-arrow-right'" aria-hidden="true"></i>{{busy && action === 'save' ? 'Saving…' : busy && action === 'review' ? 'Reviewing…' : peerId ? 'Save account policy' : 'Review changes'}}</button></div>
      </footer>
    </form>

    <div v-if="peerId && model" class="speed-account">
      <div class="speed-account__heading"><h4>Current period</h4><span class="speed-status">{{model.pending_reset ? 'Reset recovery pending' : serviceStatus}}</span></div>
      <p v-if="model.controller?.message" class="text-warning small">{{model.controller.message}}</p>
      <dl v-if="model.state" class="speed-facts">
        <div><dt>Download / upload</dt><dd>{{gb(model.state.download)}} / {{gb(model.state.upload)}} GB</dd></div>
        <div><dt>Period (Tehran)</dt><dd>{{date(model.state.start)}} – {{date(model.state.end)}}</dd></div>
        <div><dt>Last full usage reset</dt><dd>{{date(model.last_reset)}}</dd></div>
        <div><dt>Last observed renewal</dt><dd>{{date(model.last_renewal)}}</dd></div>
      </dl>
      <p v-if="model.state?.incomplete" class="small text-muted">Initial period or a sampling gap: recorded usage is incomplete.</p>
      <details class="speed-history"><summary>Reset and renewal history <span class="speed-history__count">{{model.history?.length || 0}}</span></summary><p class="small text-muted mt-2">Renewal means an observed API reset and expiry extension. Payment is not verified. Earlier dates may be unavailable.</p><ol><li v-for="(event,index) in model.history" :key="index"><div><strong>{{event.kind.replaceAll('_',' ')}}</strong><span>{{event.source}}</span></div><time>{{date(event.at)}}</time><p v-if="event.after">{{event.before || 'Initial assignment'}} → {{event.after}}</p></li></ol><p v-if="!model.history?.length" class="small text-muted">No history recorded.</p></details>
    </div>

    <Teleport to="body">
      <dialog v-if="confirm" ref="dialog" class="speed-confirm" :data-bs-theme="store.Configuration?.Server?.dashboard_theme" :aria-labelledby="id + '-confirm-title'" @cancel.prevent="dismissConfirm">
        <div class="speed-confirm__header"><span class="speed-editor__icon"><i class="bi bi-sliders" aria-hidden="true"></i></span><div><h3 :id="id + '-confirm-title'">Apply speed settings</h3><p>Choose how existing account rules are handled.</p></div></div>
        <div class="speed-confirm__body">
          <div class="speed-preview"><span>Enforcement <strong>{{master ? 'On' : 'Off'}}</strong></span><span>Default rule <strong>{{form.enabled ? 'On' : 'Off'}}</strong></span><span>Period <strong>{{form.period_days}} days</strong></span></div>
          <div class="speed-counts"><span>Following global <strong>{{preview.counts.inherit}}</strong></span><span>Custom <strong>{{preview.counts.custom}}</strong></span><span>Exempt <strong>{{preview.counts.exempt}}</strong></span></div>
          <label class="speed-choice" :class="{'is-selected': !overwrite}"><input class="form-check-input" type="radio" :value="false" v-model="overwrite" :name="id + '-overwrite'" :disabled="busy"><span><strong>Keep custom rules and exemptions</strong><small>Apply the new default to accounts following global settings.</small></span><span class="speed-choice__badge">Default</span></label>
          <label class="speed-choice" :class="{'is-selected': overwrite}"><input class="form-check-input" type="radio" :value="true" v-model="overwrite" :name="id + '-overwrite'" :disabled="busy"><span><strong>Overwrite all users</strong><small>Replace custom rules and exemptions with the global rule.</small></span></label>
          <p v-if="overwrite" class="speed-overwrite-note"><i class="bi bi-exclamation-triangle" aria-hidden="true"></i>{{preview.counts.custom + preview.counts.exempt}} individual rules will become inherited.</p>
        </div>
        <div class="speed-confirm__footer"><button autofocus class="btn btn-outline-secondary" :disabled="busy" @click="dismissConfirm">Cancel</button><button class="btn btn-primary" :disabled="busy" @click="save"><span v-if="busy" class="spinner-border spinner-border-sm" aria-hidden="true"></span>{{busy ? 'Applying…' : 'Confirm and apply'}}</button></div>
      </dialog>
    </Teleport>
  </section>
</template>

<style scoped>
.speed-editor {container: speed-editor / inline-size; border-radius:12px; overflow:clip; color:var(--bs-body-color); background:var(--bs-body-bg);}
.speed-editor:focus-visible {outline:2px solid var(--bs-primary);outline-offset:2px;}
.speed-editor__header {display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px; padding:18px 20px; border-bottom:1px solid var(--bs-border-color);}
.speed-editor__heading,.speed-confirm__header {display:flex; align-items:center; gap:12px; min-width:0;}
.speed-editor__icon,.speed-direction__icon {display:grid; place-items:center; flex:0 0 36px; height:36px; border-radius:10px; background:var(--bs-primary-bg-subtle); color:var(--bs-primary-text-emphasis); font-size:18px;}
.speed-editor h3,.speed-confirm h3 {font-size:15px; font-weight:650; margin:0; line-height:1.4;}
.speed-editor__heading p,.speed-confirm__header p {margin:3px 0 0; color:var(--bs-secondary-color); font-size:13px; line-height:1.5;}
.speed-status {display:inline-flex; align-items:center; gap:6px; border:1px solid var(--bs-border-color); border-radius:20px; padding:5px 9px; color:var(--bs-secondary-color); font-size:12px; line-height:1.4; white-space:nowrap;}
.speed-status.is-on {background:var(--bs-primary-bg-subtle); color:var(--bs-primary-text-emphasis); border-color:var(--bs-primary-border-subtle);}
.speed-feedback {margin:16px 20px; font-size:14px;}
.speed-loading {display:flex; gap:10px; align-items:center; padding:28px 20px; font-size:14px; color:var(--bs-secondary-color);}
.speed-editor fieldset {min-width:0;}
.speed-editor__form {padding:16px 20px 12px;}
.speed-controls {display:grid; grid-template-columns:minmax(0,1fr); gap:12px;}
.speed-control,.speed-mode {min-width:0; padding:12px 14px; background:var(--bs-tertiary-bg); border:1px solid var(--bs-border-color); border-radius:9px;}
.speed-control {display:flex; align-items:center; justify-content:space-between; gap:12px; cursor:pointer; margin:0; min-height:70px;}
.speed-control strong {display:block; font-size:14px; font-weight:600; line-height:1.5;}
.speed-control small {display:block; font-size:12px; line-height:1.5; color:var(--bs-secondary-color); margin-top:2px;}
.speed-control .speed-switch {float:none; flex:0 0 38px; width:38px; height:22px; min-height:0; margin:0; cursor:pointer;}
.speed-control.is-disabled {opacity:.65; cursor:default;}
.speed-mode {display:grid; gap:6px; font-size:13px; font-weight:600;}
.speed-context {display:flex; align-items:flex-start; gap:7px; color:var(--bs-secondary-color); font-size:12px; line-height:1.6; margin:10px 0 0;}
.speed-context i {flex-shrink:0;}
.speed-period {display:flex; align-items:center; justify-content:space-between; gap:16px; padding:16px 0;}
.speed-period label {font-size:13px; font-weight:600;}
.speed-period p {font-size:12px; line-height:1.5; color:var(--bs-secondary-color); margin:2px 0 0;}
.speed-period__input {flex:0 0 128px; width:128px;}
.speed-editor .form-control,.speed-editor .form-select {min-height:40px; font-size:14px; font-variant-numeric:tabular-nums;}
.speed-editor .form-control {min-width:0;}
.speed-editor .input-group-text {font-size:12px; color:var(--bs-secondary-color); background:var(--bs-tertiary-bg); padding-inline:10px;}
.speed-rule-grid {display:grid; grid-template-columns:minmax(0,1fr); gap:12px;}
.speed-direction {min-width:0; padding:14px; border:1px solid var(--bs-border-color); border-radius:10px;}
.speed-direction__header {display:flex; align-items:center; gap:9px; margin-bottom:12px;}
.speed-direction__icon {flex-basis:30px; height:30px; font-size:16px; border-radius:8px;}
.speed-direction h4,.speed-account h4 {font-size:14px; font-weight:600; margin:0; line-height:1.5;}
.speed-direction__header p {font-size:11px; color:var(--bs-secondary-color); margin:0;}
.speed-direction__rate {margin-inline-start:auto; font-size:12px; color:var(--bs-secondary-color); font-variant-numeric:tabular-nums; white-space:nowrap;}
.speed-segment {display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:3px; padding:3px; border:1px solid var(--bs-border-color); border-radius:8px; background:var(--bs-tertiary-bg); margin-bottom:12px;}
.speed-segment label {display:flex; align-items:center; justify-content:center; min-height:32px; padding:4px 8px; border:1px solid transparent; border-radius:5px; color:var(--bs-secondary-color); cursor:pointer; font-size:12px; text-align:center; line-height:1.4;}
.speed-segment .btn-check:checked + label {background:var(--bs-body-bg); border-color:var(--bs-border-color); color:var(--bs-body-color); box-shadow:0 1px 2px rgb(0 0 0 / .06);}
.speed-segment .btn-check:focus-visible + label {outline:2px solid var(--bs-primary); outline-offset:2px;}
.speed-segment .btn-check:disabled + label {cursor:default; opacity:.65;}
.speed-direction__fields {display:grid; grid-template-columns:minmax(0,1fr); gap:10px;}
.speed-direction__fields > div {min-width:0;}
.speed-label {display:block; font-size:12px; color:var(--bs-secondary-color); margin-bottom:5px; line-height:1.5;}
.speed-validation {color:var(--bs-danger-text-emphasis); font-size:13px; margin:12px 0 0;}
.speed-help {font-size:12px; color:var(--bs-secondary-color); margin-top:14px; line-height:1.6;}
.speed-help summary {cursor:pointer; width:fit-content; padding-block:4px;}
.speed-help summary i {margin-inline-end:6px;}
.speed-help ul {margin:8px 0 0; padding-inline-start:18px;}
.speed-help li + li {margin-top:4px;}
.speed-editor__footer {display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; padding:14px 20px; border-top:1px solid var(--bs-border-color); background:var(--bs-tertiary-bg);}
.speed-save-state {display:flex; align-items:center; gap:7px; font-size:12px; color:var(--bs-secondary-color); line-height:1.5;}
.speed-save-state.is-dirty {color:var(--bs-warning-text-emphasis);}
.speed-save-state.is-dirty i {font-size:6px;}
.speed-save-state.is-saved {color:var(--bs-success-text-emphasis);}
.speed-actions {display:flex; align-items:center; gap:8px; margin-inline-start:auto;}
.speed-actions .btn,.speed-confirm .btn {display:inline-flex; align-items:center; justify-content:center; gap:7px; min-height:40px; font-size:13px; border-radius:8px; padding-inline:13px;}
.speed-account {padding:16px 20px; border-top:1px solid var(--bs-border-color);}
.speed-account__heading {display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px; margin-bottom:14px;}
.speed-facts {display:grid; grid-template-columns:minmax(0,1fr); gap:12px; font-size:12px; margin-bottom:16px;}
.speed-facts dt {font-size:11px; font-weight:500; color:var(--bs-secondary-color); margin-bottom:3px;}
.speed-facts dd {margin:0; line-height:1.5; overflow-wrap:anywhere; font-variant-numeric:tabular-nums;}
.speed-history {font-size:13px; border-top:1px solid var(--bs-border-color); padding-top:12px;}
.speed-history summary {cursor:pointer; padding-block:4px;}
.speed-history__count {margin-inline-start:6px; color:var(--bs-secondary-color); font-variant-numeric:tabular-nums;}
.speed-history ol {list-style:none; margin:12px 0 0; padding:0;}
.speed-history li {padding:10px 0; border-bottom:1px solid var(--bs-border-color); font-size:12px;}
.speed-history li > div {display:flex; align-items:center; justify-content:space-between; gap:8px;}
.speed-history li span,.speed-history time {color:var(--bs-secondary-color); font-size:11px;}
.speed-history li p {margin:4px 0 0; overflow-wrap:anywhere;}
.speed-confirm {width:min(540px,calc(100% - 32px)); max-height:calc(100dvh - 32px); padding:0; border:1px solid var(--bs-border-color); border-radius:14px; color:var(--bs-body-color); background:var(--bs-body-bg); box-shadow:0 20px 64px rgb(0 0 0 / .25);}
.speed-confirm::backdrop {background:rgb(0 0 0 / .6);}
.speed-confirm__header {padding:20px; border-bottom:1px solid var(--bs-border-color);}
.speed-confirm__body {padding:16px 20px;}
.speed-preview,.speed-counts {display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; font-size:11px; line-height:1.5;}
.speed-preview {padding-bottom:12px; border-bottom:1px solid var(--bs-border-color); margin-bottom:12px;}
.speed-preview span,.speed-counts span {display:flex; flex-direction:column; gap:2px; color:var(--bs-secondary-color);}
.speed-preview strong,.speed-counts strong {color:var(--bs-body-color); font-size:13px; font-weight:600;}
.speed-counts {margin-bottom:16px;}
.speed-choice {display:flex; align-items:flex-start; gap:10px; border:1px solid var(--bs-border-color); border-radius:9px; padding:12px; margin-top:8px; cursor:pointer;}
.speed-choice.is-selected {background:var(--bs-primary-bg-subtle); border-color:var(--bs-primary-border-subtle);}
.speed-choice .form-check-input {float:none; flex:0 0 16px; width:16px; height:16px; min-height:0; margin:3px 0 0;}
.speed-choice > span {min-width:0;}
.speed-choice strong {display:block; font-size:13px; font-weight:600; line-height:1.5;}
.speed-choice small {display:block; font-size:12px; color:var(--bs-secondary-color); line-height:1.5; margin-top:3px;}
.speed-choice .speed-choice__badge {margin-inline-start:auto; font-size:10px; color:var(--bs-primary-text-emphasis); padding:3px 6px; border:1px solid var(--bs-primary-border-subtle); border-radius:5px;}
.speed-overwrite-note {display:flex; align-items:flex-start; gap:7px; margin:12px 0 0; font-size:12px; color:var(--bs-warning-text-emphasis); line-height:1.5;}
.speed-confirm__footer {display:flex; justify-content:flex-end; gap:8px; padding:14px 20px; border-top:1px solid var(--bs-border-color); background:var(--bs-tertiary-bg);}
@container speed-editor (min-width:540px) {.speed-controls,.speed-rule-grid,.speed-facts {grid-template-columns:repeat(2,minmax(0,1fr));}}
@container speed-editor (min-width:800px) {.speed-direction__fields {grid-template-columns:repeat(2,minmax(0,1fr));}}
@container speed-editor (max-width:400px) {.speed-editor__header,.speed-editor__form,.speed-editor__footer,.speed-account {padding-inline:14px;}.speed-editor__heading {align-items:flex-start;}.speed-period {gap:8px;}.speed-period__input {flex-basis:112px;width:112px;}.speed-period p {max-width:22ch;}.speed-actions {width:100%;}.speed-actions .btn {flex:1;}.speed-direction__header p {display:none;}}
@media (pointer:coarse) {.speed-editor .form-control,.speed-editor .form-select,.speed-actions .btn,.speed-confirm .btn {min-height:44px;}.speed-segment label {min-height:38px;}}
</style>
