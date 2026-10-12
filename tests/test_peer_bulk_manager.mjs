#!/usr/bin/env node
// Does not touch the running VPN, HTTP endpoints, or persistent databases.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {peerPolicy, filterAndSortPeers, plannedJob} from "../src/static/app/src/utilities/peerBulkPolicy.mjs";
const now=new Date(2026,9,12,12,0,0);
const format = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")} ${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}:00`;
const dateJob = (days, id="date-rule") => ({
  JobID:id, Field:"date",Operator:"lgt",Action:"restrict",
  Value:format(new Date(now.getTime()+days*86400000)),
  CreationDate:"2026-10-01 12:00:00", ExpireDate:null
});
const trafficJob = (limit, weight=1,id="quota-rule") => ({
  JobID:id,Field:"quota_total_data",Operator:"lgt",Action:"restrict",
  Value:JSON.stringify({limit_gb:String(limit),weight:String(weight)}),
  CreationDate:"2026-10-01 12:00:00",ExpireDate:null
});
const p=(id, jobs, used=0,restricted=false)=>({
  id, name:id, allowed_ip:"10.1.1.1/32",metered_data:used,
  jobs, restricted
});
const peers=[
  p("time-exhausted",[dateJob(-1)]),
  p("traffic-exhausted",[trafficJob(10)],10,true),
  p("both-unlimited",[]),
  p("time-soon",[dateJob(1)]),
  p("quota-soon",[trafficJob(10)],9.5),
  p("quota-many",[trafficJob(10),trafficJob(15)],2),
  p("legacy",[ {Field:"total_data", Action:"restrict", ExpireDate:null}], 100),
  p("date-legacy",[{...dateJob(5), Operator:"lte"}]),
  p("both-limited",[dateJob(10), trafficJob(50)],5),
];
const opts = status => filterAndSortPeers(peers,{status,sort:"name"},now.getTime()).map(x=>x.peer.id);
assert.deepEqual(opts("time_expired"),["time-exhausted"]);
assert.deepEqual(opts("traffic_expired"),["traffic-exhausted"]);
assert.deepEqual(opts("expired"),["time-exhausted","traffic-exhausted"].sort());
assert.deepEqual(opts("unlimited"),["both-unlimited"]);
assert.deepEqual(opts("expiring_soon"),["quota-soon","time-soon"]);
assert.deepEqual(opts("restricted"),["traffic-exhausted"]);
assert.deepEqual(opts("unknown"),["date-legacy","legacy","quota-many"]);
assert.equal(peerPolicy(peers[4],now.getTime()).trafficRemainingGb,0.5);
assert.equal(peerPolicy(peers[5],now.getTime()).traffic,"unknown");
const ordered=filterAndSortPeers(peers,{sort:"traffic_remaining"},now.getTime());
assert.equal(ordered[0].peer.id,"traffic-exhausted");
const preserved=plannedJob(peers[8],"wg0","traffic",10,now,()=>assert.fail("Should edit existing job"));
assert.equal(preserved.JobID,"quota-rule");
assert.equal(JSON.parse(preserved.Value).limit_gb,"60");
assert.equal(JSON.parse(preserved.Value).weight,"1");
const created=plannedJob(peers[2],"wg0","traffic",10,now,()=>"new-job");
assert.equal(created.JobID,"new-job");
assert.equal(JSON.parse(created.Value).limit_gb,"10");
const extended=plannedJob(peers[3],"wg0","time",30,now,()=>assert.fail("existing"));
assert.equal(extended.JobID,"date-rule");
assert.ok(new Date(extended.Value.replace(" ","T")).getTime()>now.getTime()+30*86400000);
assert.throws(()=>plannedJob(peers[5],"wg0","traffic",10,now,()=>""),/ambiguous/);
assert.throws(()=>plannedJob(peers[2],"wg0","time",0,now,()=>""),/positive/);

// Never alter the server API's routes, contracts or peer-limit algorithm.
// The bulk manager uses only pre-existing GET/POST endpoints.
const ui=readFileSync("src/static/app/src/components/configurationComponents/peerListComponents/peerBulkManager.vue","utf8");
const list=readFileSync("src/static/app/src/components/configurationComponents/peerList.vue","utf8");
const endpoints=[
  "/api/getWireguardConfigurationInfo", "/api/savePeerScheduleJob",
  "/api/updateWireguardConfigurationInfo", "restrictPeers", "allowAccessPeers", "deletePeers"
];
for(const e of endpoints) assert.ok(ui.includes(e), `Missing legacy endpoint ${e}`);
for(const e of ["api/ui/getWireguardConfigurationPage", "peerPageSize = 50"]) assert.ok(list.includes(e));
assert.match(ui,/type="checkbox"/);
assert.match(ui,/Select all/);
assert.match(ui,/DELETE/);
assert.match(ui,/Checking current peer state/);
assert.match(ui,/maxOperation = 200/);
console.log("PASS: UI-only bulk selection, quota/time classification, limits and existing API contracts");
