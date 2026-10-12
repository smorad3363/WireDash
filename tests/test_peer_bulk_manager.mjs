#!/usr/bin/env node
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {DEFAULT_SERVER_TIME_ZONE, filterAndSortPeers, peerPolicy, plannedJob, validTimeZone}
  from "../src/static/app/src/utilities/peerBulkPolicy.mjs";
import {renewRestrictedWithRestore} from "../src/static/app/src/utilities/peerBulkRenewal.mjs";
import {peerDataBar, peerStatus, peerTimeLabel} from "../src/static/app/src/utilities/peerBulkDisplay.mjs";

// Browser running in UTC or Europe/Berlin must schedule the server's local wall
// time (default Europe/Istanbul) identically.
const now = new Date("2026-10-12T09:00:00Z"); // Istanbul noon, Berlin 11am
const when = days => {
  const d = new Date(Date.UTC(2026,9,12+days,12,0,0));
  return d.toISOString().slice(0,19).replace("T"," ");
};
const dateJob = (days, id="date-rule") => ({
  JobID:id, Field:"date",Operator:"lgt",Action:"restrict",
  Value:when(days),CreationDate:"2026-10-01 12:00:00",ExpireDate:null
});
const trafficJob = (limit, weight=1,id="quota-rule") => ({
  JobID:id,Field:"quota_total_data",Operator:"lgt",Action:"restrict",
  Value:JSON.stringify({limit_gb:String(limit),weight:String(weight)}),
  CreationDate:"2026-10-01 12:00:00",ExpireDate:null
});
const peer = (id,jobs=[],{raw=0,weight=1,restricted=false}={}) => ({
  id,name:id,allowed_ip:"10.1.1.1/32",jobs,restricted,
  total_receive:raw/2,total_sent:raw/2,cumu_receive:0,cumu_sent:0,
  metered_data:raw*weight
});
const sample=[
  peer("time-exhausted",[dateJob(-1)]),
  peer("traffic-exhausted",[trafficJob(10)],{raw:10,restricted:true}),
  peer("open-unlimited"),
  peer("time-soon",[dateJob(1)]),
  peer("quota-soon",[trafficJob(10)],{raw:9.5}),
  peer("quota-many",[trafficJob(10),trafficJob(15)],{raw:2}),
  peer("legacy",[{Field:"total_data",Action:"restrict",ExpireDate:null}],{raw:100}),
  peer("date-legacy",[{...dateJob(5),Operator:"lst"}]),
  peer("both-limited",[dateJob(10),trafficJob(50)],{raw:5}),
  peer("closed-time",[{...dateJob(-3),ExpireDate:"2026-10-10 12:00:00"}],{restricted:true}),
  peer("closed-traffic",[{...trafficJob(10,2),ExpireDate:"2026-10-10 12:00:00"}],{raw:12,weight:2,restricted:true}),
  peer("manual-restricted",[],{restricted:true}),
  peer("restricted-with-time",[dateJob(5)],{restricted:true}),
];
const opt = status => filterAndSortPeers(sample,{status,sort:"name"},now.getTime()).map(x=>x.peer.id);
assert.deepEqual(opt("time_expired"),["time-exhausted"]);
assert.deepEqual(opt("traffic_expired"),["traffic-exhausted"]);
assert.deepEqual(opt("expired"),["time-exhausted","traffic-exhausted"].sort());
assert.deepEqual(opt("unlimited"),["open-unlimited"]);
assert.deepEqual(opt("expiring_soon"),["quota-soon","time-soon"]);
assert.deepEqual(opt("restricted"),["closed-time","closed-traffic","manual-restricted",
  "restricted-with-time","traffic-exhausted"]);
assert.deepEqual(opt("unknown").sort(),["closed-time","closed-traffic","date-legacy",
  "legacy","manual-restricted","quota-many","restricted-with-time","traffic-exhausted"].sort());
for (const id of ["closed-time","closed-traffic","manual-restricted"]) {
  const p=sample.find(x=>x.id===id);
  assert.equal(peerPolicy(p,now.getTime()).time,"unknown");
  assert.equal(peerPolicy(p,now.getTime()).traffic,"unknown");
}
assert.equal(peerPolicy(sample[4],now.getTime()).trafficRemainingGb,0.5);
assert.equal(peerPolicy(sample[5],now.getTime()).traffic,"unknown");
const ordered=filterAndSortPeers(sample,{sort:"traffic_remaining"},now.getTime());
assert.equal(ordered[0].peer.id,"traffic-exhausted");

// Extending a future expiry starts from old deadline, while finished or absent
// rules start from NOW; both are expressed in the server's timezone.
assert.ok(validTimeZone("Europe/Istanbul"));
assert.equal(validTimeZone("Not/A_Zone"),false);
assert.equal(DEFAULT_SERVER_TIME_ZONE,"Europe/Istanbul");
const extended=plannedJob(sample[3],"wg0","time",30,now,()=>assert.fail("existing"));
assert.equal(extended.JobID,"date-rule");
assert.equal(extended.Value,when(31));
const past=plannedJob(sample[0],"wg0","time",30,now,()=>assert.fail("existing"));
assert.equal(past.Value,when(30));
const renewedRestricted=plannedJob(sample[9],"wg0","time",30,now,()=>"new-limit");
assert.equal(renewedRestricted.JobID,"new-limit");
assert.equal(renewedRestricted.Value,when(30));
assert.equal(renewedRestricted.Peer,"closed-time");
// Different browser timezone cannot silently shift the server-side deadline.
const berlin=plannedJob(peer("eu", [dateJob(1)]),"wg0","time",1,
  new Date("2026-10-12T09:00:00Z"),()=>"",{serverTimeZone:"Europe/Istanbul"});
assert.equal(berlin.Value,when(2));
// DST zone: add one wall-clock day rather than browser-local 24h drift.
const dst=plannedJob(peer("dst"),"wg0","time",1,new Date("2026-03-28T11:00:00Z"),
  ()=>"dst-job",{serverTimeZone:"Europe/Berlin"});
assert.equal(dst.Value,"2026-03-29 12:00:00");

// Adding quota always gives the requested ADDITIONAL metered GB even if usage
// has already exceeded the old limit; no counter reset or factor loss.
const limited=plannedJob(sample[8],"wg0","traffic",10,now,()=>assert.fail("existing"));
assert.equal(limited.JobID,"quota-rule");
assert.equal(JSON.parse(limited.Value).limit_gb,"60");
const exceeded=plannedJob(sample[1],"wg0","traffic",5,now,()=>assert.fail("existing"));
assert.equal(JSON.parse(exceeded.Value).limit_gb,"15");
const historical=plannedJob(sample[10],"wg0","traffic",5,now,()=>"new-quota");
assert.equal(historical.JobID,"new-quota");
assert.equal(JSON.parse(historical.Value).weight,"2");
assert.equal(JSON.parse(historical.Value).limit_gb,"29");
assert.throws(()=>plannedJob(sample[11],"wg0","traffic",5,now,()=>""),/weight 1 consent/);
const zero=plannedJob(sample[11],"wg0","traffic",5,now,()=>"z",
  {assumeWeightOne:true});
assert.equal(JSON.parse(zero.Value).weight,"1");
assert.equal(JSON.parse(zero.Value).limit_gb,"5");
assert.throws(()=>plannedJob(sample[5],"wg0","traffic",10,now,()=>""),/Ambiguous/);
assert.throws(()=>plannedJob(sample[7],"wg0","time",10,now,()=>""),/Unsupported/);
assert.throws(()=>plannedJob(sample[2],"wg0","time",0,now,()=>""),/positive/);
assert.throws(()=>plannedJob(sample[2],"wg0","time",5,now,()=>"a",
  {serverTimeZone:"Invalid/TimeZone"}),/Invalid server timezone/);

// Compact table UI: a SINGLE combined bar with the ratio inside it.
// The blue and teal portions MUST correspond to already-weighted counters,
// and no quota may be invented for expired/legacy restricted peers.
const used15=peer("mixed-traffic",[trafficJob(20)],{raw:15});
used15.metered_receive=3; // Upload
used15.metered_sent=12;   // Download
const model=peerDataBar(used15,peerPolicy(used15,now.getTime()));
assert.equal(model.label,"15|20");
assert.equal(model.hasQuota,true);
assert.equal(model.used,15);
assert.equal(model.quota,20);
assert.equal(model.percent,75);
assert.equal(model.downloadPct,60);
assert.equal(model.uploadPct,15);
assert.equal(model.otherPct,0);
assert.match(model.details,/Download: 12 GB/);
assert.match(model.details,/Upload: 3 GB/);
assert.match(model.details,/Remaining: 5 GB/);
const saturated=peer("overlimit",[trafficJob(10)],{raw:13});
saturated.metered_receive=3;saturated.metered_sent=10;
const full=peerDataBar(saturated,peerPolicy(saturated,now.getTime()));
assert.equal(full.label,"13|10");
assert.equal(full.percent,100);
assert.equal(full.downloadPct+full.uploadPct,100);
assert.equal(full.state,"exhausted");
const unrestricted=peer("unlimited",[],{raw:4});
unrestricted.metered_receive=1;unrestricted.metered_sent=3;
const infinity=peerDataBar(unrestricted,peerPolicy(unrestricted,now.getTime()));
assert.equal(infinity.label,"4|∞");
assert.equal(infinity.percent,0);
assert.equal(infinity.hasQuota,false);
const historic=peer("historic",[],{raw:4,restricted:true});
const unknown=peerDataBar(historic,peerPolicy(historic,now.getTime()));
assert.equal(unknown.label,"4|?");
assert.equal(unknown.state,"unknown");
assert.equal(unknown.downloadPct,0);
assert.equal(unknown.hasQuota,false);
const both=peer("both-expired",[dateJob(-1),trafficJob(5)],{raw:6});
assert.deepEqual(peerStatus(peerPolicy(both,now.getTime())),
  {key:"both",label:"Expired · Both",rank:0});
assert.equal(peerStatus(peerPolicy(sample[0],now.getTime())).label,"Expired · Time");
assert.equal(peerStatus(peerPolicy(sample[1],now.getTime())).label,"Expired · Data");
assert.equal(peerStatus(peerPolicy(sample[9],now.getTime())).label,"Needs review");
assert.equal(peerTimeLabel(peerPolicy(sample[3],now.getTime())),"1d left");

// Clicking an existing header twice reverses sorting and preserves active
// selection (the table's stable key is the peer ID, not the row position).
const sortingPeers=[sample[0],sample[1],sample[3],sample[4],sample[8],sample[9],sample[2]];
const sorted=(sort,direction) => filterAndSortPeers(sortingPeers,
  {sort,direction},now.getTime()).map(x=>x.peer.id);
assert.deepEqual(sorted("name","asc"), [...sorted("name","desc")].reverse());
assert.equal(sorted("time_remaining","asc")[0],"time-exhausted");
assert.equal(sorted("time_remaining","desc")[0],"both-limited");
assert.deepEqual(sorted("time_remaining","desc").slice(-4).sort(),
  ["closed-time","open-unlimited","quota-soon","traffic-exhausted"].sort());
assert.deepEqual(sorted("traffic_remaining","asc").slice(0,3),
  ["traffic-exhausted","quota-soon","both-limited"]);
assert.deepEqual(sorted("traffic_remaining","desc").slice(0,3),
  ["both-limited","quota-soon","traffic-exhausted"]);
assert.equal(sorted("status","asc")[0],"time-exhausted");
assert.equal(sorted("status","desc")[0],"open-unlimited");
assert.equal(sorted("access","desc")[0],"traffic-exhausted");
assert.equal(sorted("access","asc")[0],"both-limited");

// Simulate old endpoints, including partial HTTP failures and a failed
// access-restoration attempt. No live users, keys or network calls involved.
const simulate = async ({consent=true, allow=true, save=true, restrict=true, firstReadFails=false}={}) => {
  let isRestricted = true, reads=0;
  const calls=[];
  const post=async (url) => {
    calls.push(url);
    if (url.includes("/allowAccessPeers/")) {
      isRestricted=false; // worst case: HTTP reports failure AFTER peer moved
      return {status:allow, message:allow?"OK":"Allow response failed"};
    }
    if (url === "/api/savePeerScheduleJob") return {status:save, message:save?"OK":"Save failed"};
    if (url.includes("/restrictPeers/")) {
      if (restrict) isRestricted=true;
      return {status:restrict};
    }
    throw new Error("Unexpected endpoint "+url);
  };
  const get=async () => {
    reads++;
    if(firstReadFails && reads===1) throw new Error("GET failed");
    return {status:true,data:{configurationRestrictedPeers:isRestricted?[{id:"p1"}]:[]}};
  };
  const result=await renewRestrictedWithRestore({
    id:"p1",configuration:"wg0",job:{JobID:"job1"},consent,post,get
  });
  return {result,calls,isRestricted,reads};
};
const success=await simulate();
assert.deepEqual(success.result,{saved:true,restored:true,error:""});
assert.equal(success.isRestricted,true);
assert.deepEqual(success.calls,[
  "/api/allowAccessPeers/wg0","/api/savePeerScheduleJob","/api/restrictPeers/wg0"
]);
const saveFailed=await simulate({save:false});
assert.equal(saveFailed.result.saved,false);
assert.equal(saveFailed.result.restored,true);
assert.equal(saveFailed.isRestricted,true);
assert.match(saveFailed.result.error,/Save failed/);
const allowFailed=await simulate({allow:false});
assert.equal(allowFailed.result.saved,false);
assert.equal(allowFailed.result.restored,true);
assert.equal(allowFailed.isRestricted,true);
assert.equal(allowFailed.calls.includes("/api/savePeerScheduleJob"),false);
const restrictFailed=await simulate({restrict:false});
assert.equal(restrictFailed.result.saved,true);
assert.equal(restrictFailed.result.restored,false);
assert.equal(restrictFailed.isRestricted,false);
assert.match(restrictFailed.result.error,/CRITICAL/);
const firstGetFailure=await simulate({firstReadFails:true});
assert.equal(firstGetFailure.result.restored,true);
assert.equal(firstGetFailure.isRestricted,true);
const noConsent=await simulate({consent:false});
assert.equal(noConsent.result.saved,false);
assert.equal(noConsent.result.restored,true);
assert.equal(noConsent.calls.length,0);
assert.match(noConsent.result.error,/consent/);

// Static guard: existing endpoints and explicit consent/compensation, without
// ever changing the backend routes or bot-facing response payloads.
const ui=readFileSync("src/static/app/src/components/configurationComponents/peerListComponents/peerBulkManager.vue","utf8");
const list=readFileSync("src/static/app/src/components/configurationComponents/peerList.vue","utf8");
for(const e of ["/api/getWireguardConfigurationInfo","/api/savePeerScheduleJob",
  "/api/updateWireguardConfigurationInfo",
  "restrictPeers","deletePeers"]) assert.ok(ui.includes(e),e);
const legacyRenewalFlow=readFileSync("src/static/app/src/utilities/peerBulkRenewal.mjs","utf8");
assert.ok(legacyRenewalFlow.includes("/api/allowAccessPeers"));
assert.ok(legacyRenewalFlow.includes("/api/restrictPeers"));
assert.match(ui,/temporarily allowing each restricted peer/);
assert.match(ui,/temporaryAccessConsent/);
assert.match(ui,/Restoring restriction/);
assert.match(ui,/CRITICAL: Restricted peer/);
const flow=readFileSync("src/static/app/src/utilities/peerBulkRenewal.mjs","utf8");
assert.match(flow,/CRITICAL: original restriction could not be verified/);
assert.match(ui,/renewalPreview/);
assert.match(ui,/serverTimeZone/);
assert.match(ui,/maxOperation = 200/);
assert.match(ui,/toggleSort\('name'\)/);
assert.match(ui,/toggleSort\('status'\)/);
assert.match(ui,/toggleSort\('time_remaining'\)/);
assert.match(ui,/toggleSort\('traffic_remaining'\)/);
assert.match(ui,/toggleSort\('access'\)/);
assert.match(ui,/:aria-sort="ariaSort\('name'\)"/);
assert.match(ui,/bulk-data-bar__label/);
assert.match(ui,/bulk-data-bar__download/);
assert.match(ui,/bulk-data-bar__upload/);
assert.match(ui,/bulk-status--/);
assert.match(ui,/<option value="expired">Expired<\/option>/);
assert.match(ui,/<option value="unknown">Needs review<\/option>/);
assert.doesNotMatch(ui,/<option value="time_expired">/);
assert.doesNotMatch(ui,/<option value="traffic_expired">/);
assert.doesNotMatch(ui,/<span class="small text-muted">Sort<\/span>/);
assert.match(ui,/background: var\(--bs-body-bg/);
assert.match(ui,/color: var\(--bs-body-color/);
assert.match(ui,/focus-visible/);
assert.match(readFileSync("src/static/app/src/utilities/peerBulkDisplay.mjs","utf8"),/Download:.*blue/);
for(const text of ["api/ui/getWireguardConfigurationPage","peerPageSize = 50"])
  assert.ok(list.includes(text));
console.log("PASS: safe historical expiry labels, renewal quotas, timezone, restrict compensation and unchanged APIs");
