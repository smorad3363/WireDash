#!/usr/bin/env node
// Offline test: QR must use configuration-level metadata, and failed requests
// must not sign out a valid administrator. No real API, secrets or VPN needed.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const qr = readFileSync("src/static/app/src/components/configurationComponents/peerQRCode.vue", "utf8");
const modals = readFileSync("src/static/app/src/components/configurationComponents/peerListComponents/peerListModals.vue", "utf8");
const peers = readFileSync("src/static/app/src/components/configurationComponents/peerList.vue", "utf8");
const readModel = readFileSync("src/modules/ConfigurationReadModel.py", "utf8");

assert.match(readModel, /if k != "configuration"/);
assert.doesNotMatch(qr, /selectedPeer\.configuration/);
assert.match(qr, /this\.configurationInfo\.Protocol/);
assert.match(qr, /this\.configurationInfo\.ListenPort/);
assert.match(qr, /errorMessage/);
assert.match(qr, /Could not load the peer configuration for QR/);
assert.match(modals, /<PeerQRCodeModal[\s\S]*?:configurationInfo="configurationInfo"/);
assert.match(peers, /<PeerListModals[\s\S]*?:configurationInfo="configurationInfo"/);

const original = readFileSync("src/static/app/src/utilities/fetch.js", "utf8");
const src = original
    .replace(/^import .*;\s*$/gm, "")
    .replace(/export const /g, "const ")
    .replace(/import\.meta\.env\.MODE/g, '"development"')
    + "\n globalThis._testExports = {fetchGet, fetchPost};\n";
let response, fetchError, activeCrossServer = false;
const messages = [], redirects = [];
const context = {
    URLSearchParams,
    console: {error: () => {}},
    DashboardConfigurationStore: () => ({
        getActiveCrossServer: () => activeCrossServer ? {host: "https://remote.example", apiKey: "test"} : undefined,
        newMessage: (...args) => messages.push(args)
    }),
    router: {push: async target => {redirects.push(target)}},
    fetch: async () => {
        if (fetchError) throw new Error("network offline");
        return response;
    }
};
vm.runInNewContext(src, context);
const {fetchGet, fetchPost} = context._testExports;
const fake = (status, body) => ({
    status, ok: status >= 200 && status < 300,
    json: async () => body
});
const run = async (method, code, body) => {
    response = fake(code, body);
    let result = "not-called";
    await method("/api/downloadPeer/wg0", {id:"peer1"}, v => {result = v});
    return result;
};

// A normal successful QR download returns its profile without navigation.
let ok = await run(fetchGet, 200, {status:true, data:{file:"[Interface]\nPrivateKey = TEST"}});
assert.equal(ok.status, true);
assert.equal(redirects.length, 0);

// Errors from QR/config generation do not terminate the admin session.
for (const code of [400, 403, 404, 500, 502]) {
    const err = await run(fetchGet, code, {status:false,message:"Peer configuration unavailable"});
    assert.equal(err.status, false);
    assert.equal(err.message, "Peer configuration unavailable");
    assert.equal(redirects.length, 0);
}
const post = await run(fetchPost, 500, {status:false,message:"Temporary failure"});
assert.equal(post.status, false);
assert.equal(redirects.length, 0);

fetchError = true;
let networkResult;
await fetchGet("/api/downloadPeer/wg0", {id:"peer1"}, v => networkResult=v);
assert.equal(networkResult.status, false);
assert.match(networkResult.message,/Could not reach the server/);
assert.equal(redirects.length, 0);
fetchError = false;

response = fake(200, {status: true, data:{file:"ok"}});
await fetchGet("/api/downloadPeer/wg0", {}, () => {throw new Error("QR callback rendering problem")}).then(
    () => assert.fail("Expected callback to throw"),
    e => assert.match(e.message, /QR callback rendering problem/)
);
assert.equal(redirects.length, 0);

// Only an actual local 401 should redirect to sign-in.
await run(fetchGet, 401, {status:false,message:"Unauthorized access."});
assert.equal(redirects.length, 1);
assert.equal(redirects[0].path, "/signin");
assert.equal(messages.length, 1);

// Remote server API-key failures must not redirect the local browser.
activeCrossServer = true;
await run(fetchGet, 401, {status:false,message:"API Key does not exist"});
assert.equal(redirects.length, 1);

console.log("PASS: peer QR metadata and API errors do not cause false logout");
