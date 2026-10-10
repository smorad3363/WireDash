<script setup>
import {onMounted, ref} from "vue";
import {fetchGet, fetchPost} from "@/utilities/fetch.js";

const status = ref(null)
const loading = ref(true)
const busy = ref(false)
const message = ref("")
const messageError = ref(false)
const interval = ref(30)
const token = ref("")
const chat = ref("")
const restoreName = ref("")
const confirmation = ref("")
const password = ref("")
const totp = ref("")

const notify = (res, fallback) => {
  messageError.value = !res?.status
  message.value = res?.message || (res?.status ? fallback : "Backup action failed")
}
const refresh = async () => {
  loading.value = true
  await fetchGet("/api/ui/systemBackup", {}, (res) => {
    if (res?.status) {
      status.value = res.data
      interval.value = res.data.intervalMinutes
      messageError.value = false
    } else {
      status.value = null
      notify(res, "Backup service unavailable")
    }
    loading.value = false
  })
}
const execute = async (operation, fields = {}, text = "Updated") => {
  if (busy.value) return
  busy.value = true
  message.value = ""
  await fetchPost("/api/ui/systemBackup", {operation, ...fields}, (res) => {
    notify(res, text)
    if (res?.status) {
      if (res.data?.archives) {
        status.value = res.data
        interval.value = res.data.intervalMinutes
      }
      if (operation === "set_telegram") token.value = ""
      if (operation === "restore") {
        password.value = ""
        totp.value = ""
        confirmation.value = ""
      }
    }
  })
  busy.value = false
}
const saveTelegram = async () => {
  if (!token.value || !chat.value) return
  await execute("set_telegram", {token: token.value, chat: chat.value},
    "Telegram destination verified; scheduled backup enabled")
}
const verify = (name) => execute("verify", {name}, "Archive and database integrity verified")
const initiateRestore = async () => {
  if (!restoreName.value || confirmation.value !== `RESTORE ${restoreName.value}` || !password.value) return
  const selected = restoreName.value
  await execute("restore", {
    name: selected, confirmation: confirmation.value,
    password: password.value, totp: totp.value
  }, "Restore queued on the host. The VPN and panel may disconnect while state is replaced.")
}
const displayDate = (epoch) => epoch ? new Date(epoch * 1000).toLocaleString() : "Never"
const fileSize = (bytes) => (bytes / 1048576).toFixed(2) + " MiB"
onMounted(refresh)
</script>

<template>
  <div class="card rounded-3">
    <div class="card-header d-flex justify-content-between align-items-center">
      <h6 class="my-2"><i class="bi bi-cloud-arrow-up me-2"></i>Automatic Backup & Full Restore</h6>
      <button class="btn btn-sm btn-outline-secondary" type="button" :disabled="loading || busy"
              @click="refresh"><i class="bi bi-arrow-clockwise me-1"></i>Refresh status</button>
    </div>
    <div class="card-body d-flex flex-column gap-3">
      <p class="text-muted mb-0 small">Full server backup (WireGuard, AmneziaWG, users and databases).
        Uses the existing host <code>wireback</code> and Telegram delivery. Backups never restart the VPN.</p>
      <div v-if="message" class="alert py-2 mb-0" :class="messageError ? 'alert-danger' : 'alert-info'" role="status">{{message}}</div>
      <div v-if="loading">Loading backup status...</div>
      <div v-else-if="!status" class="alert alert-warning mb-0">
        Host backup control is not connected. Upgrade the managed installation with the standard WireDash install command.
        Backup and restore are unavailable until it is installed.
      </div>
      <template v-else>
        <div class="row g-2 small">
          <div class="col-md-4"><strong>Telegram:</strong> {{status.configured ? "Configured" : "Not configured"}}</div>
          <div class="col-md-4"><strong>Schedule:</strong> {{status.enabled ? "Enabled" : "Disabled"}}</div>
          <div class="col-md-4"><strong>Last sent:</strong> {{displayDate(status.lastSuccessEpoch)}}</div>
          <div class="col-md-6"><strong>Last attempted:</strong> {{displayDate(status.lastAttemptEpoch)}}</div>
          <div class="col-md-6"><strong>Last failure:</strong> {{displayDate(status.lastFailureEpoch)}}</div>
        </div>
        <div class="border rounded-3 p-3 d-flex flex-column gap-2">
          <h6 class="mb-0">Telegram delivery</h6>
          <div class="row g-2">
            <div class="col-md-7">
              <label class="form-label small">Bot token (never shown again)</label>
              <input class="form-control" type="password" autocomplete="new-password" v-model="token"
                     placeholder="123456:TelegramBotToken">
            </div>
            <div class="col-md-5">
              <label class="form-label small">Chat ID / @channel</label>
              <input class="form-control" type="text" autocomplete="off" v-model="chat">
            </div>
          </div>
          <div><button class="btn btn-sm btn-primary" type="button" :disabled="busy || !token || !chat"
                       @click="saveTelegram">Verify & save Telegram</button></div>
          <small class="text-muted">Send /start to your bot before saving. Credentials are stored on the server only.</small>
        </div>
        <div class="border rounded-3 p-3 d-flex flex-column gap-2">
          <h6 class="mb-0">Automatic schedule</h6>
          <div class="d-flex flex-wrap align-items-end gap-2">
            <label class="small">Interval (minutes, 5–1440)
              <input class="form-control mt-1" type="number" min="5" max="1440" step="1"
                     style="max-width:180px" v-model.number="interval">
            </label>
            <button class="btn btn-sm btn-outline-primary" :disabled="busy || interval < 5 || interval > 1440"
                    @click="execute('set_interval', {minutes: interval}, 'Interval saved')">Save interval</button>
            <button class="btn btn-sm btn-success" :disabled="busy || !status.configured"
                    @click="execute('enable', {}, 'Backups enabled')">Enable</button>
            <button class="btn btn-sm btn-outline-danger" :disabled="busy"
                    @click="execute('disable', {}, 'Backups disabled')">Disable</button>
            <button class="btn btn-sm btn-primary" :disabled="busy || !status.configured"
                    @click="execute('backup_now', {}, 'Backup queued; refresh later to see Telegram delivery')">Backup now</button>
          </div>
        </div>
        <div class="border rounded-3 p-3 d-flex flex-column gap-2">
          <h6 class="mb-0">Local full backups</h6>
          <div v-if="!status.archives.length" class="text-muted small">No full backups stored locally. Telegram configuration and the first backup may be needed.</div>
          <div v-for="file in status.archives" :key="file.name"
               class="d-flex flex-wrap align-items-center gap-2 border-bottom py-2 small">
            <div class="flex-grow-1">
              <strong>{{file.name}}</strong>
              <div class="text-muted">{{fileSize(file.bytes)}} • {{displayDate(file.modified)}} • {{file.telegramSent ? "Sent to Telegram" : "Local only"}}</div>
            </div>
            <button class="btn btn-sm btn-outline-secondary" :disabled="busy || !file.sha256"
                    @click="verify(file.name)">Verify</button>
            <button class="btn btn-sm btn-outline-danger" :disabled="busy || !file.sha256"
                    @click="restoreName = file.name; confirmation = ''; password = ''; totp = ''">Restore...</button>
          </div>
        </div>
        <div v-if="restoreName" class="border border-danger rounded-3 p-3 d-flex flex-column gap-2">
          <h6 class="text-danger mb-0">Full system restore — destructive</h6>
          <p class="small mb-0">Restoring <strong>{{restoreName}}</strong> will stop the panel and VPN, replace current server
            data and may require client reconnection. The archive is verified before the panel stops.
            A pre-restore snapshot is saved locally. Only proceed in a maintenance window.</p>
          <label class="small">Type exactly: <code>RESTORE {{restoreName}}</code>
            <input class="form-control mt-1" autocomplete="off" v-model="confirmation">
          </label>
          <label class="small">Current administrator password
            <input class="form-control mt-1" type="password" autocomplete="current-password" v-model="password">
          </label>
          <label class="small">Current MFA code (if enabled)
            <input class="form-control mt-1" autocomplete="one-time-code" v-model="totp">
          </label>
          <div class="d-flex gap-2">
            <button class="btn btn-danger" type="button" :disabled="busy || !password || confirmation !== `RESTORE ${restoreName}`"
                    @click="initiateRestore">Verify & start full restore</button>
            <button class="btn btn-outline-secondary" :disabled="busy" @click="restoreName = ''">Cancel</button>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>
