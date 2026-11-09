<script setup>
import {ref,onMounted,nextTick} from 'vue'
import {getUrl} from '@/utilities/fetch.js'
import {DashboardConfigurationStore} from '@/stores/DashboardConfigurationStore.js'
const props=defineProps({peerId:String,configuration:String})
const emit=defineEmits(['saved'])
const model=ref(null), form=ref(null), csrf=ref(''), error=ref(''), busy=ref(false), confirm=ref(false), overwrite=ref(false)
const dialog=ref(null),returnFocus=ref(null)
const mode=ref('inherit'), master=ref(false), preview=ref(null)
const store=DashboardConfigurationStore()
const date=sec=>sec?new Intl.DateTimeFormat(undefined,{dateStyle:'medium',timeStyle:'short',timeZone:'Asia/Tehran'}).format(new Date(sec*1000)):'Not recorded'
const gb=value=>Number(value||0).toFixed(2)
const endpoint=()=>props.peerId?`/api/ui/speed/peer/${encodeURIComponent(props.configuration)}`:'/api/ui/speed/settings'
async function request(path,body){
  if(store.getActiveCrossServer())throw new Error('Sign in directly to this server to manage speed policies.')
  const response=await fetch(getUrl(path),{credentials:'same-origin',method:body?'POST':'GET',headers:body?{'Content-Type':'application/json','X-Speed-CSRF':csrf.value}:{},body:body?JSON.stringify(body):undefined})
  const result=await response.json()
  if(!response.ok||!result.status)throw new Error(result.message||'Request failed')
  csrf.value=result.csrf||csrf.value
  return result.data
}
async function load(){
  busy.value=true;error.value=''
  try{
    const data=await request(endpoint()+(props.peerId?'?id='+encodeURIComponent(props.peerId):''))
    model.value=data
    form.value=structuredClone(data.policy||{enabled:false,period_days:30,download_gb:30,upload_gb:30,download_mbps:null,upload_mbps:null})
    mode.value=data.mode||'inherit';master.value=data.master_enabled||false
  }catch(e){error.value=e.message}finally{busy.value=false}
}
function closeConfirm(){confirm.value=false;returnFocus.value?.focus()}
function trapFocus(event){
  if(event.key!=='Tab')return
  const controls=dialog.value?.querySelectorAll('input:not(:disabled),button:not(:disabled)')
  if(!controls?.length)return
  const first=controls[0],last=controls[controls.length-1]
  if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}
  if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}
}
async function review(){
  busy.value=true;error.value=''
  try{returnFocus.value=document.activeElement;preview.value=await request('/api/ui/speed/settings');overwrite.value=false;confirm.value=true;await nextTick();dialog.value?.querySelector('input')?.focus()}
  catch(e){error.value=e.message}finally{busy.value=false}
}
async function save(){
  busy.value=true;error.value=''
  try{
    const body=props.peerId?{id:props.peerId,mode:mode.value,policy:form.value,revision:model.value.revision}:{policy:form.value,revision:preview.value.revision,overwrite:overwrite.value,master_enabled:master.value}
    await request(endpoint(),body);closeConfirm();await load();emit('saved')
  }catch(e){error.value=e.message;confirm.value=false}finally{busy.value=false}
}
onMounted(load)
</script>
<template>
<section class="card rounded-3 speed-editor" aria-label="Speed policy">
  <div class="card-header"><h6 class="my-2">{{peerId?'Account speed policy':'Speed policies and renewal history'}}</h6></div>
  <div class="card-body">
    <p v-if="error" class="alert alert-danger" role="alert">{{error}}</p>
    <p v-if="!model&&!error" role="status">Loading…</p>
    <form v-if="model&&form" @submit.prevent="peerId?save():review()">
      <fieldset :disabled="busy">
        <template v-if="!peerId">
          <label class="form-check mb-3"><input class="form-check-input" type="checkbox" v-model="master"><span class="form-check-label">Enable speed enforcement (master control)</span></label>
          <p class="small text-muted">Turning master control off removes managed speed limits and keeps saved rules. Account expiry and paid quotas remain active.</p>
        </template>
        <label v-if="peerId" class="d-block mb-3">Policy mode
          <select v-model="mode" class="form-select"><option value="inherit">Follow global settings</option><option value="custom">Custom settings</option><option value="exempt">Exempt from speed reduction</option></select>
        </label>
        <fieldset :disabled="peerId&&mode!=='custom'">
          <label class="form-check mb-3"><input class="form-check-input" type="checkbox" v-model="form.enabled"><span class="form-check-label">Enable this rule after its thresholds</span></label>
          <label class="d-block mb-3">Period (days)<input class="form-control" type="number" min="1" max="366" step="1" required v-model.number="form.period_days"></label>
          <div class="row g-3">
            <div v-for="direction in ['download','upload']" :key="direction" class="col-12 col-md-6">
              <div class="border rounded-3 p-3 h-100">
                <h6 class="text-capitalize">Client {{direction}}</h6>
                <label class="d-block mb-2">Threshold (GB)<input class="form-control" type="number" min="0.000001" max="1000000" step="any" required v-model.number="form[direction+'_gb']"></label>
                <label class="form-check mb-2"><input class="form-check-input" type="checkbox" :checked="form[direction+'_mbps']===null" @change="form[direction+'_mbps']=$event.target.checked?null:1"><span class="form-check-label">No speed cap in this direction</span></label>
                <label v-if="form[direction+'_mbps']!==null" class="d-block">Speed after threshold (Mbps)<input class="form-control" type="number" min="0.001" max="100000" step="any" required v-model.number="form[direction+'_mbps']"></label>
              </div>
            </div>
          </div>
        </fieldset>
        <p class="small text-muted mt-3">Download and upload thresholds are independent. Full usage reset starts a fresh period; restarting the server does not. Changing period length takes effect at the next boundary. Saving a rule keeps current consumption.</p>
        <button class="btn btn-primary" type="submit">{{busy?'Saving…':peerId?'Save account policy':'Review global changes'}}</button>
        <button class="btn btn-outline-secondary ms-2" type="button" @click="load">Reload</button>
      </fieldset>
    </form>
    <template v-if="peerId&&model">
      <hr><p class="small mb-1">Controller: {{model.controller?.status||'awaiting sample'}} <span v-if="model.pending_reset">· Reset recovery pending</span></p>
      <p v-if="model.controller?.message" class="text-warning small">{{model.controller.message}}</p>
      <dl class="speed-facts" v-if="model.state">
        <div><dt>Period download / upload</dt><dd>{{gb(model.state.download)}} / {{gb(model.state.upload)}} GB</dd></div>
        <div><dt>Period start / end (Tehran)</dt><dd>{{date(model.state.start)}} / {{date(model.state.end)}}</dd></div>
        <div><dt>Last full usage reset</dt><dd>{{date(model.last_reset)}}</dd></div>
        <div><dt>Last observed renewal</dt><dd>{{date(model.last_renewal)}}</dd></div>
      </dl>
      <p v-if="model.state?.incomplete" class="small text-muted">Initial period or a sampling gap: recorded usage is incomplete. Older traffic has not been assigned to this period.</p>
      <details><summary>Reset and renewal history</summary>
        <p class="small text-muted mt-2">Renewal means an observed API reset and expiry extension. Payment is not verified. Dates before history recording may be unavailable.</p>
        <ol class="ps-3"><li v-for="(event,index) in model.history" :key="index" class="small mb-2"><strong>{{event.kind.replaceAll('_',' ')}}</strong> · {{date(event.at)}} · {{event.source}}
          <div v-if="event.after">{{event.before||'Initial assignment'}} → {{event.after}}</div></li></ol>
        <p v-if="!model.history?.length" class="small">No history recorded.</p>
      </details>
    </template>
  </div>
  <div v-if="confirm" ref="dialog" class="speed-confirm" role="dialog" aria-modal="true" aria-labelledby="speed-confirm-title" @keydown.esc="closeConfirm" @keydown="trapFocus">
    <div class="card shadow p-4 m-auto"><h5 id="speed-confirm-title">Apply global speed settings</h5>
      <p>Inherit: {{preview.counts.inherit}} · Custom: {{preview.counts.custom}} · Exempt: {{preview.counts.exempt}}</p>
      <label class="form-check mb-2"><input class="form-check-input" type="radio" :value="false" v-model="overwrite" name="speed-overwrite"><span class="form-check-label">Keep custom rules and exemptions</span></label>
      <label class="form-check mb-3"><input class="form-check-input" type="radio" :value="true" v-model="overwrite" name="speed-overwrite"><span class="form-check-label">Overwrite all users with the global rule</span></label>
      <p v-if="overwrite" class="small text-warning">{{preview.counts.custom+preview.counts.exempt}} individual rules will become inherited.</p>
      <div class="d-flex gap-2"><button autofocus class="btn btn-outline-secondary" :disabled="busy" @click="closeConfirm">Cancel</button><button class="btn btn-primary" :disabled="busy" @click="save">Confirm and apply</button></div>
    </div>
  </div>
</section>
</template>
<style scoped>
.speed-editor{position:relative}.speed-facts{font-size:.8rem;display:grid;gap:.5rem}.speed-facts dd{margin:0;overflow-wrap:anywhere}.speed-confirm{position:fixed;inset:0;z-index:1080;background:rgb(0 0 0 / .65);display:flex;padding:16px;overflow:auto}.speed-confirm>.card{max-width:520px;width:100%}button,input,select{min-height:40px}@media(pointer:coarse){button,input,select{min-height:44px}}
</style>
