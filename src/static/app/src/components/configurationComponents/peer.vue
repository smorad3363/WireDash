<script setup>
import {computed, ref} from 'vue'
import {onClickOutside} from '@vueuse/core'
import PeerSettingsDropdown from '@/components/configurationComponents/peerSettingsDropdown.vue'
import PeerTagBadge from '@/components/configurationComponents/peerTagBadge.vue'
import {GetLocale} from '@/utilities/locale.js'

const props = defineProps({Peer: Object, ConfigurationInfo: Object, order: Number, searchPeersLength: Number})
const emit = defineEmits(['details', 'qrcode', 'configurationFile', 'setting', 'jobs', 'refresh', 'share', 'assign'])
const menuTarget = ref(null)
const menuOpen = ref(false)
const expanded = ref(false)
onClickOutside(menuTarget, () => {menuOpen.value = false})
const time = computed(() => props.Peer.card_policy?.time || {state: 'unknown'})
const traffic = computed(() => props.Peer.card_policy?.traffic || {state: 'unknown'})
const status = computed(() => props.Peer.restricted ? 'Restricted' : props.Peer.status === 'running' ? 'Connected' : 'Offline')
const groups = computed(() => Object.values(props.ConfigurationInfo?.Info?.PeerGroups || {}).filter(g => g.Peers?.includes(props.Peer.id)))
const gb = value => Number.isFinite(Number(value)) ? Number(value).toLocaleString(undefined, {maximumFractionDigits: 2}) : '—'
const date = value => {
  if (!value) return GetLocale('Not recorded')
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? GetLocale('Not recorded') : new Intl.DateTimeFormat(undefined,
    {year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'}).format(d)
}
const remainingTime = computed(() => {
  const t = time.value
  if (t.historical) return GetLocale('Last recorded expiry')
  if (t.state === 'unlimited') return GetLocale('No time limit')
  if (t.state === 'unknown') return GetLocale('Not recorded')
  if (t.remaining_seconds <= 0) return GetLocale('Expired')
  const days = Math.floor(t.remaining_seconds / 86400)
  return days ? `${days} ${GetLocale('days left')}` : `${Math.max(1, Math.ceil(t.remaining_seconds / 3600))} ${GetLocale('hours left')}`
})
const quotaTitle = computed(() => traffic.value.historical ? GetLocale('Last recorded quota') :
  traffic.value.kind === 'upload' ? GetLocale('Upload limit') :
  traffic.value.kind === 'download' ? GetLocale('Download limit') : GetLocale('Traffic quota'))
const handshake = computed(() => {
  const value = props.Peer.latest_handshake
  return !value || ['N/A', 'No Handshake'].includes(value) ? GetLocale('Never connected') : `${value.split(',')[0]} ${GetLocale('ago')}`
})
const progress = computed(() => Math.max(0, Math.min(100, Number(traffic.value.percent) || 0)))
const canExport = computed(() => !props.Peer.restricted && !!props.Peer.private_key)
</script>

<template>
  <article class="peer-card card" :id="'peer_' + Peer.id" :class="{'is-restricted': Peer.restricted}">
    <header class="peer-card__header">
      <div class="peer-card__identity">
        <span class="peer-card__status" :class="{'is-connected': !Peer.restricted && Peer.status === 'running', 'is-locked': Peer.restricted}">
          <i :class="Peer.restricted ? 'bi bi-lock' : 'bi bi-circle-fill'" aria-hidden="true"></i>{{GetLocale(status)}}
        </span>
        <h3 class="peer-card__name" :title="Peer.name">{{Peer.name || GetLocale('Untitled Peer')}}</h3>
        <code class="peer-card__ip" dir="ltr">{{Peer.allowed_ip}}</code>
      </div>
      <div ref="menuTarget" class="peer-card__menu">
        <button class="peer-card__icon" type="button" :aria-label="GetLocale('More actions') + ': ' + (Peer.name || GetLocale('Untitled Peer'))"
          :aria-expanded="menuOpen" @click="menuOpen = !menuOpen"><i class="bi bi-three-dots" aria-hidden="true"></i></button>
        <PeerSettingsDropdown v-if="menuOpen" :Peer="Peer" :ConfigurationInfo="ConfigurationInfo" :dropup="false"
          @qrcode="emit('qrcode')" @configurationFile="emit('configurationFile')" @setting="emit('setting')"
          @jobs="emit('jobs')" @refresh="menuOpen = false; emit('refresh')" @share="emit('share')" @assign="emit('assign')" />
      </div>
    </header>

    <div class="peer-card__usage">
      <div class="peer-card__usage-heading">
        <span>{{GetLocale('Total usage')}}</span>
        <strong>{{gb(Peer.metered_data)}} <small>GB</small></strong>
      </div>
      <div v-if="traffic.limit_gb != null" class="peer-card__quota">
        <div class="peer-card__quota-caption">
          <span>{{quotaTitle}} · {{gb(traffic.used_gb)}} / {{gb(traffic.limit_gb)}} GB</span>
          <span>{{gb(traffic.remaining_gb)}} GB {{GetLocale('left')}}</span>
        </div>
        <div class="peer-card__progress" role="progressbar" :aria-label="quotaTitle" :aria-valuenow="progress" aria-valuemin="0" aria-valuemax="100"
          :class="{'is-warning': progress >= 80, 'is-exhausted': progress >= 100}">
          <span :style="{width: progress + '%'}"></span>
        </div>
      </div>
      <p v-else class="peer-card__quota-note">{{GetLocale(traffic.state === 'unlimited' ? 'No volume limit' : 'Quota not recorded')}}</p>
      <div class="peer-card__directions">
        <span><i class="bi bi-arrow-down" aria-hidden="true"></i>{{GetLocale('Download')}} <strong>{{gb(Peer.metered_sent)}} GB</strong></span>
        <span><i class="bi bi-arrow-up" aria-hidden="true"></i>{{GetLocale('Upload')}} <strong>{{gb(Peer.metered_receive)}} GB</strong></span>
      </div>
    </div>

    <dl class="peer-card__facts">
      <div><dt>{{GetLocale('Expiry')}}</dt><dd :class="{'text-danger-emphasis': time.state === 'expired'}">{{remainingTime}}</dd>
        <dd class="peer-card__secondary" v-if="time.expires_at">{{date(time.expires_at)}}</dd></div>
      <div><dt>{{GetLocale('Last connection')}}</dt><dd>{{handshake}}</dd>
        <dd class="peer-card__secondary" v-if="Peer.endpoint && Peer.endpoint !== 'N/A'" dir="ltr">{{Peer.endpoint}}</dd></div>
      <div class="peer-card__created"><dt>{{GetLocale(Peer.creation_basis === 'created' ? 'Created' : 'First record')}}</dt>
        <dd>{{date(Peer.created_at)}}</dd></div>
    </dl>

    <div class="peer-card__tags" v-if="groups.length">
      <PeerTagBadge v-for="group in groups" :key="group.GroupName" :BackgroundColor="group.BackgroundColor" :GroupName="group.GroupName" :Icon="'bi-' + group.Icon" />
    </div>
    <div v-if="expanded" class="peer-card__details">
      <dl><div><dt>{{GetLocale('Public Key')}}</dt><dd><code dir="ltr">{{Peer.id}}</code></dd></div>
        <div><dt>DNS</dt><dd>{{Peer.DNS || '—'}}</dd></div>
        <div><dt>MTU</dt><dd>{{Peer.mtu || ConfigurationInfo?.Info?.OverridePeerSettings?.MTU || '—'}}</dd></div>
        <div v-if="Peer.notes"><dt>{{GetLocale('Notes')}}</dt><dd class="peer-card__notes">{{Peer.notes}}</dd></div>
      </dl>
      <button v-if="!Peer.restricted" type="button" class="peer-card__history" @click="emit('details')">{{GetLocale('Traffic and connection history')}} <i class="bi bi-arrow-up-right" aria-hidden="true"></i></button>
    </div>
    <footer class="peer-card__actions">
      <button type="button" :aria-expanded="expanded" @click="expanded = !expanded"><i class="bi bi-info-circle" aria-hidden="true"></i>{{GetLocale(expanded ? 'Less' : 'Details')}}</button>
      <button type="button" :disabled="Peer.restricted" :title="Peer.restricted ? GetLocale('Allow access before editing') : GetLocale('Edit peer')" @click="emit('setting')"><i class="bi bi-sliders" aria-hidden="true"></i>{{GetLocale('Edit')}}</button>
      <button type="button" :disabled="!canExport" :title="GetLocale('Download configuration')" @click="emit('configurationFile')"><i class="bi bi-download" aria-hidden="true"></i>{{GetLocale('Config')}}</button>
      <button type="button" class="peer-card__qr" :disabled="!canExport" :aria-label="GetLocale('QR code')" @click="emit('qrcode')"><i class="bi bi-qr-code" aria-hidden="true"></i></button>
    </footer>
  </article>
</template>

<style scoped>
.peer-card {height:100%; min-width:0; border:1px solid var(--bs-border-color); border-radius:14px; background:var(--bs-body-bg); box-shadow:0 2px 6px rgb(0 0 0 / 5%); color:var(--bs-body-color); font-size:13px;}
.peer-card.is-restricted {border-color:var(--bs-border-color); border-inline-start:3px solid var(--bs-warning);}
.peer-card__header {display:flex; align-items:flex-start; gap:12px; padding:16px 16px 12px;}
.peer-card__identity {min-width:0; flex:1;}
.peer-card__status {display:inline-flex; align-items:center; gap:6px; font-size:11px; font-weight:650; color:var(--bs-secondary-color); margin-bottom:8px;}
.peer-card__status i {font-size:7px;}
.peer-card__status.is-connected {color:var(--bs-success-text-emphasis);}
.peer-card__status.is-locked {color:var(--bs-warning-text-emphasis);}
.peer-card__status.is-locked i {font-size:12px;}
.peer-card__name {font-size:15px; font-weight:650; line-height:1.4; margin:0 0 4px; overflow-wrap:anywhere;}
.peer-card__ip {font-size:12px; color:var(--bs-secondary-color);}
.peer-card__menu {position:relative; margin-top:-5px; margin-inline-end:-7px;}
.peer-card__menu :deep(.dropdown-menu) {inset-inline-end:0; left:auto; top:34px; z-index:20;}
.peer-card__icon {width:36px; height:36px; border:0; border-radius:8px; color:var(--bs-secondary-color); background:transparent;}
.peer-card__usage {margin:0 16px; padding:12px; background:var(--bs-tertiary-bg); border-radius:10px;}
.peer-card__usage-heading {display:flex; align-items:baseline; justify-content:space-between; gap:8px;}
.peer-card__usage-heading>span {color:var(--bs-secondary-color); font-size:12px;}
.peer-card__usage-heading>strong {font-size:21px; font-weight:650; font-variant-numeric:tabular-nums; letter-spacing:-.02em;}
.peer-card__usage-heading small {font-size:12px; font-weight:500; color:var(--bs-secondary-color);}
.peer-card__quota {margin-top:8px;}
.peer-card__quota-caption {display:flex; justify-content:space-between; gap:6px; font-size:11px; color:var(--bs-secondary-color); flex-wrap:wrap;}
.peer-card__progress {height:5px; margin-top:7px; border-radius:10px; background:var(--bs-secondary-bg); overflow:hidden;}
.peer-card__progress>span {display:block; height:100%; border-radius:inherit; background:var(--bs-primary);}
.peer-card__progress.is-warning>span {background:var(--bs-warning);}
.peer-card__progress.is-exhausted>span {background:var(--bs-danger);}
.peer-card__quota-note {font-size:11px; color:var(--bs-secondary-color); margin:3px 0 0;}
.peer-card__directions {display:flex; justify-content:space-between; gap:8px; margin-top:10px; font-size:11px; flex-wrap:wrap;}
.peer-card__directions>span {display:flex; gap:4px; align-items:center; color:var(--bs-secondary-color);}
.peer-card__directions strong {color:var(--bs-body-color); font-weight:550; font-variant-numeric:tabular-nums;}
.peer-card__directions .bi-arrow-down {color:var(--bs-primary-text-emphasis);}
.peer-card__directions .bi-arrow-up {color:var(--bs-success-text-emphasis);}
.peer-card__facts {display:grid; grid-template-columns:1fr 1fr; gap:12px; margin:0; padding:14px 16px;}
.peer-card dt {font-size:11px; font-weight:450; color:var(--bs-secondary-color); margin-bottom:4px;}
.peer-card dd {font-size:12px; margin:0; font-variant-numeric:tabular-nums; overflow-wrap:anywhere;}
.peer-card dd.peer-card__secondary {font-size:11px; margin-top:3px; color:var(--bs-secondary-color);}
.peer-card__created {grid-column:1 / -1; display:flex; justify-content:space-between; gap:8px; padding-top:10px; border-top:1px solid var(--bs-border-color-translucent);}
.peer-card__created dt {margin:0;}
.peer-card__created dd {font-size:11px; color:var(--bs-secondary-color); text-align:end;}
.peer-card__tags {display:flex; flex-wrap:wrap; gap:5px; margin:0 16px 12px;}
.peer-card__actions {display:flex; gap:4px; padding:8px; border-top:1px solid var(--bs-border-color-translucent); margin-top:auto;}
.peer-card__actions button {display:flex; align-items:center; justify-content:center; gap:6px; flex:1; min-height:36px; border:0; border-radius:7px; color:var(--bs-secondary-color); background:transparent; font-size:12px;}
.peer-card__actions button.peer-card__qr {flex:0 0 36px;}
.peer-card button:disabled {opacity:.4; cursor:not-allowed;}
.peer-card button:focus-visible {outline:2px solid var(--bs-primary); outline-offset:2px;}
.peer-card__details {margin:0 16px 14px; padding-top:12px; border-top:1px solid var(--bs-border-color-translucent);}
.peer-card__details dl {display:grid; gap:10px; margin:0;}
.peer-card__details code {font-size:11px; overflow-wrap:anywhere;}
.peer-card__notes {white-space:pre-wrap;}
.peer-card__history {border:0; background:transparent; color:var(--bs-primary-text-emphasis); padding:12px 0 0; font-size:12px;}
@media (hover:hover) and (pointer:fine) {.peer-card button:not(:disabled):hover {background:var(--bs-secondary-bg); color:var(--bs-body-color);}}
@media (pointer:coarse) {.peer-card__actions button,.peer-card__icon {min-height:44px; min-width:44px;}}
</style>
