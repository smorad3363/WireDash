#!/usr/bin/env node
// Exercise the real editor orchestration without credentials or a live API.
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import vm from 'node:vm'
const vue = readFileSync('src/static/app/src/components/settingsComponent/speedPolicyEditor.vue', 'utf8')
const script = vue.match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
const policy = {enabled: false, period_days: 30, download_gb: 30, upload_gb: 30, download_mbps: 5, upload_mbps: 2}
function editor(peer = false) {
  let settings = {policy: structuredClone(policy), revision: 3, master_enabled: true, counts: {inherit: 10, custom: 2, exempt: 1}}
  let account = {policy: structuredClone(policy), revision: 4, mode: 'inherit'}
  let fail = false, opened = false
  const posts = [], emitted = []
  const context = {
    defineProps: () => peer ? {peerId: 'key+/=', configuration: 'wg-test'} : {},
    defineEmits: () => (...args) => emitted.push(args),
    useId: () => 'editor-test', onMounted: () => {}, nextTick: async () => {},
    ref: value => ({value}), computed: fn => ({get value() {return fn()}}),
    structuredClone, Intl, Number, JSON, encodeURIComponent,
    DashboardConfigurationStore: () => ({getActiveCrossServer: () => undefined}),
    getUrl: value => value,
    document: {activeElement: {isConnected: true, disabled: false, focus() {}}},
    fetch: async (url, options) => {
      const isPeer = url.includes('/peer/')
      if (options.method === 'POST') {
        posts.push({url, headers: options.headers, body: JSON.parse(options.body)})
        if (fail) return {ok: false, json: async () => ({status: false, message: 'Settings changed; reload before applying'})}
        if (isPeer) account = {...account, ...posts.at(-1).body, revision: account.revision + 1}
        else settings = {...settings, ...posts.at(-1).body, revision: settings.revision + 1}
      }
      return {ok: true, json: async () => ({status: true, csrf: 'test-token', data: structuredClone(isPeer ? account : settings)})}
    }
  }
  const api = vm.runInNewContext(script + ';\n({load,review,save,dismissConfirm,setUnlimited,model,form,master,mode,preview,overwrite,dialog,panel,canSave,hasChanges,notice,error})', context)
  api.dialog.value = {showModal() {opened = true}, close() {opened = false}}
  api.panel.value = {focus() {}}
  return {api, posts, emitted, get opened() {return opened}, failSave() {fail = true}, editServer() {settings.revision++}}
}
const global = editor()
await global.api.load()
assert.equal(global.api.master.value, true)
assert.equal(global.api.hasChanges.value, false)
assert.equal(global.posts.length, 0)
global.api.setUnlimited('download', true)
assert.equal(global.api.form.value.download_mbps, null)
global.api.setUnlimited('download', false)
assert.equal(global.api.form.value.download_mbps, 5)
assert.equal(global.api.hasChanges.value, false)
global.api.master.value = false
global.editServer()
await global.api.review()
assert.equal(global.opened, true)
assert.equal(global.api.overwrite.value, false)
assert.equal(global.posts.length, 0)
await global.api.dismissConfirm()
assert.equal(global.opened, false)
assert.equal(global.posts.length, 0)
await global.api.review()
await global.api.save()
assert.equal(global.posts.length, 1)
assert.equal(global.posts[0].headers['X-Speed-CSRF'], 'test-token')
assert.equal(global.posts[0].body.revision, 4)
assert.equal(global.posts[0].body.master_enabled, false)
assert.equal(global.posts[0].body.overwrite, false)
assert.deepEqual(global.posts[0].body.policy, policy)
assert.equal(global.api.hasChanges.value, false)
assert.equal(global.api.notice.value, 'Speed settings saved.')
assert.equal(global.opened, false)
// The alternative overwrite choice is sent only after explicit selection.
global.api.form.value.download_gb = 40
await global.api.review()
global.api.overwrite.value = true
await global.api.save()
assert.equal(global.posts[1].body.overwrite, true)
// A stale/failed save retains edits and never reports success.
global.api.form.value.upload_gb = 50
global.failSave()
await global.api.review()
await global.api.save()
assert.equal(global.api.hasChanges.value, true)
assert.match(global.api.error.value, /reload/)
assert.equal(global.api.notice.value, '')
assert.equal(global.opened, false)
const account = editor(true)
await account.api.load()
account.api.mode.value = 'exempt'
await account.api.save()
assert.equal(account.posts[0].body.mode, 'exempt')
assert.equal(account.posts[0].body.revision, 4)
assert.equal('overwrite' in account.posts[0].body, false)
assert.equal('master_enabled' in account.posts[0].body, false)
assert.equal(account.api.mode.value, 'exempt')
assert.equal(account.api.hasChanges.value, false)
console.log('PASS editor load, saved settings, rate memory, preserve/overwrite/cancel, CSRF, stale-save feedback and per-account payload')
