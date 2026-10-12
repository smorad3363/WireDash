// Presentation + preview for the EXISTING peer and schedule-job APIs.
// The legacy peer payload contains ACTIVE jobs, not finished job history.
// A restricted peer with no active rule is UNKNOWN, never "unlimited".
const TRAFFIC_FIELDS = new Set(["quota_total_data", "total_data", "total_receive", "total_sent"]);
const EXPIRED = "expired";
export const DEFAULT_SERVER_TIME_ZONE = "Europe/Istanbul";

export function validTimeZone(timeZone) {
  try { new Intl.DateTimeFormat("en-US", {timeZone}).format(new Date()); return true; }
  catch { return false; }
}

// Scheduled-job values are naive timestamps compared with datetime.now() on
// the SERVER. Compare and extend wall-clock timestamps in the configured
// server timezone, never the administrator's browser timezone.
const formatWall = millis => {
  const d = new Date(millis), pad = n => String(n).padStart(2,"0");
  return [d.getUTCFullYear(),pad(d.getUTCMonth()+1),pad(d.getUTCDate())].join("-") +
    " " + [pad(d.getUTCHours()),pad(d.getUTCMinutes()),pad(d.getUTCSeconds())].join(":");
};
const parseWall = value => {
  if (typeof value !== "string" || !/^\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d$/.test(value)) return NaN;
  const normalized = value.replace("T"," ");
  const ms = Date.parse(normalized.replace(" ","T") + "Z");
  return Number.isFinite(ms) && formatWall(ms) === normalized ? ms : NaN;
};
const nowWall = (now, timeZone) => {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone, year:"numeric", month:"2-digit", day:"2-digit", hour:"2-digit",
    minute:"2-digit", second:"2-digit", hourCycle:"h23"
  });
  const vals = Object.fromEntries(fmt.formatToParts(new Date(now))
    .filter(p => p.type !== "literal").map(p => [p.type, Number(p.value)]));
  return Date.UTC(vals.year, vals.month-1, vals.day, vals.hour, vals.minute, vals.second);
};
const activeRules = peer => Array.isArray(peer?.jobs)
  ? peer.jobs.filter(j => j && !j.ExpireDate)
  : null;
const finiteNonnegative = n => typeof n === "number" && Number.isFinite(n) && n >= 0;

export function peerPolicy(peer, now = Date.now(), serverTimeZone = DEFAULT_SERVER_TIME_ZONE) {
  const restricted = !!peer?.restricted;
  const result = {
    time: "unknown", traffic: "unknown", timeRemainingDays:null,
    trafficRemainingGb:null, deadline:null, quotaLimitGb:null, weight:null,
    timeJob:null, quotaJob:null, restricted
  };
  const rules = activeRules(peer);
  if (!rules) return result;
  const timeRules = rules.filter(j => j.Field === "date");
  const trafficRules = rules.filter(j => TRAFFIC_FIELDS.has(j.Field));
  // A completed job is absent from peer.jobs. Restricted state gives no
  // reliable reason (manual, time, quota, other): never claim unlimited.
  if (!timeRules.length) result.time = restricted ? "unknown" : "unlimited";
  else if (timeRules.length === 1 && timeRules[0].Action === "restrict" &&
    timeRules[0].Operator === "lgt" && validTimeZone(serverTimeZone)) {
    const deadline = parseWall(timeRules[0].Value);
    if (Number.isFinite(deadline)) {
      const diff = deadline - nowWall(now, serverTimeZone);
      result.timeJob = timeRules[0];
      result.deadline = deadline;
      result.timeRemainingDays = diff / 86400000;
      result.time = diff <= 0 ? EXPIRED : diff <= 3*86400000 ? "soon" : "limited";
    }
  }
  if (!trafficRules.length) result.traffic = restricted ? "unknown" : "unlimited";
  else if (trafficRules.length === 1 && trafficRules[0].Field === "quota_total_data" &&
    trafficRules[0].Action === "restrict" && trafficRules[0].Operator === "lgt") {
    try {
      const raw = JSON.parse(trafficRules[0].Value);
      const limit = Number(raw.limit_gb), weight = Number(raw.weight);
      const used = peer.metered_data;
      if (Number.isFinite(limit) && limit > 0 && limit <= 1000000 &&
          Number.isFinite(weight) && weight >= 0.1 && weight <= 10 &&
          finiteNonnegative(used)) {
        result.quotaJob = trafficRules[0];
        result.quotaLimitGb = limit;
        result.weight = weight;
        result.trafficRemainingGb = limit - used;
        result.traffic = used >= limit ? EXPIRED : limit-used <= 1 ? "soon" : "limited";
      }
    } catch { /* unrecognised policy => unknown */ }
  }
  return result;
}

export function filterAndSortPeers(peers, {
  status = "all", query = "", sort = "name", serverTimeZone = DEFAULT_SERVER_TIME_ZONE
} = {}, now = Date.now()) {
  const needle = String(query).trim().toLowerCase();
  const marked = peers.map(peer => ({peer, policy:peerPolicy(peer, now, serverTimeZone)}));
  const matches = marked.filter(({peer,policy}) => {
    if (needle && ![peer.name,peer.id,peer.allowed_ip].some(x =>
      String(x ?? "").toLowerCase().includes(needle))) return false;
    switch (status) {
      case "expired": return policy.time === EXPIRED || policy.traffic === EXPIRED;
      case "time_expired": return policy.time === EXPIRED;
      case "traffic_expired": return policy.traffic === EXPIRED;
      case "expiring_soon": return policy.time === "soon" || policy.traffic === "soon";
      case "unlimited": return !peer.restricted && policy.time === "unlimited" && policy.traffic === "unlimited";
      case "unlimited_time": return policy.time === "unlimited";
      case "unlimited_traffic": return policy.traffic === "unlimited";
      case "restricted": return !!peer.restricted;
      case "active": return !peer.restricted && policy.time !== EXPIRED && policy.traffic !== EXPIRED &&
        policy.time !== "unknown" && policy.traffic !== "unknown";
      case "unknown": return policy.time === "unknown" || policy.traffic === "unknown";
      default: return true;
    }
  });
  const metric = x => {
    switch(sort) {
      case "time_remaining": return x.policy.timeRemainingDays ?? Infinity;
      case "traffic_remaining": return x.policy.trafficRemainingGb ?? Infinity;
      case "usage": return -Number(x.peer.metered_data || 0);
      default: return null;
    }
  };
  matches.sort((a,b) => {
    const av=metric(a),bv=metric(b);
    if (av !== null && bv !== null && av !== bv) return av-bv;
    return String(a.peer.name || "").localeCompare(String(b.peer.name || "")) ||
      String(a.peer.id).localeCompare(String(b.peer.id));
  });
  return matches;
}

// There is no API field exposing the persisted metering weight after a quota
// job was closed. For nonzero usage we can infer it from the existing raw and
// metered counters. At zero usage it is unidentifiable and requires opt-in to
// use weight 1; never silently overwrite an unknown historical weight.
const inferWeight = (peer, assumeWeightOne) => {
  const counters=["total_receive","total_sent","cumu_receive","cumu_sent"];
  if (!counters.every(k => finiteNonnegative(peer[k]))) {
    throw new Error("Raw usage counters missing; cannot preserve the traffic weight.");
  }
  const raw = counters.reduce((v,k)=>v+peer[k],0);
  const billed = peer.metered_data;
  if (!finiteNonnegative(billed)) throw new Error("Metered usage is missing.");
  if (raw === 0) {
    if (billed !== 0) throw new Error("Metering data is inconsistent.");
    if (!assumeWeightOne) throw new Error("Previous traffic factor cannot be determined at zero usage; enable explicit weight 1 consent.");
    return 1;
  }
  const ratio = billed/raw;
  const weight = Number(ratio.toPrecision(12));
  if (!Number.isFinite(weight) || weight < 0.1 || weight > 10 ||
    Math.abs(raw*weight-billed) > Math.max(1e-8, billed*1e-8)) {
    throw new Error("Traffic weight cannot be reliably inferred; edit this peer manually.");
  }
  return weight;
};

export function plannedJob(peer, configuration, action, amount, now = new Date(),
  newId = () => "", options = {}) {
  const {serverTimeZone = DEFAULT_SERVER_TIME_ZONE, assumeWeightOne = false} = options;
  if (!validTimeZone(serverTimeZone)) throw new Error("Invalid server timezone.");
  const value = Number(amount);
  if (!Number.isFinite(value) || value <= 0 ||
    (action === "time" && (!Number.isInteger(value) || value > 3650)) ||
    (action === "traffic" && value > 1000000)) {
    throw new Error("Enter a positive amount (max 3650 days or 1000000 GB).");
  }
  const policy = peerPolicy(peer, now.getTime(), serverTimeZone);
  let existing, jobValue, field;
  if (action === "time") {
    if (policy.time === "unknown" && policy.timeJob !== null) throw new Error("Ambiguous time policy; edit manually.");
    // A restricted peer whose completed time rule disappeared has an unknown
    // historical cause. A new rule can be created, but do not infer that it
    // automatically restores access.
    if (activeRules(peer)?.some(j => j.Field === "date") && !policy.timeJob)
      throw new Error("Unsupported time rule; edit manually.");
    existing = policy.timeJob;
    const baseline = Math.max(nowWall(now.getTime(), serverTimeZone),
      policy.deadline ?? -Infinity);
    jobValue = formatWall(baseline + value*86400000);
    field = "date";
  } else if (action === "traffic") {
    if (activeRules(peer)?.some(j => TRAFFIC_FIELDS.has(j.Field)) && !policy.quotaJob)
      throw new Error("Ambiguous traffic policy; edit manually.");
    existing = policy.quotaJob;
    const weight = existing ? policy.weight : inferWeight(peer, assumeWeightOne);
    // When a limit was exceeded before renewal, grant the FULL added quota as
    // remaining usage; otherwise increase the previous limit by the amount.
    const base = Math.max(existing ? policy.quotaLimitGb : 0, peer.metered_data);
    const limit = Number((base + value).toPrecision(14));
    if (!Number.isFinite(limit) || limit <= 0 || limit > 1000000)
      throw new Error("New traffic quota exceeds 1000000 GB.");
    jobValue = JSON.stringify({limit_gb:String(limit),weight:String(weight)});
    field = "quota_total_data";
  } else throw new Error("Unsupported action.");
  return {
    JobID:existing ? existing.JobID : newId(), Configuration:configuration,
    Peer:peer.id, Field:field, Operator:"lgt", Value:jobValue,
    CreationDate:existing?.CreationDate || "", ExpireDate:null, Action:"restrict"
  };
}
