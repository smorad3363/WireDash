// UI-only policy presentation. Uses fields from the EXISTING peer-list API.
// Never infer "unlimited" if jobs are absent or rules are unrecognised.
const DATE_FIELD = "date";
const TRAFFIC_FIELDS = new Set(["quota_total_data", "total_data", "total_receive", "total_sent"]);
const EXPIRED = "expired";

const stamp = value => {
  if (typeof value !== "string" || !/^\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d$/.test(value)) return NaN;
  const time = new Date(value.replace(" ", "T")).getTime();
  return Number.isFinite(time) ? time : NaN;
};
const activeRules = peer => Array.isArray(peer?.jobs)
  ? peer.jobs.filter(j => j && !j.ExpireDate && j.Action === "restrict")
  : null;

export function peerPolicy(peer, now = Date.now()) {
  const rules = activeRules(peer);
  const result = {
    time: "unknown", traffic: "unknown", timeRemainingDays: null,
    trafficRemainingGb: null, deadline: null, quotaLimitGb: null, weight: null,
    timeJob: null, quotaJob: null, restricted: !!peer?.restricted
  };
  if (!rules) return result;
  const dateJobs = rules.filter(j => j.Field === DATE_FIELD);
  const trafficJobs = rules.filter(j => TRAFFIC_FIELDS.has(j.Field));
  const otherDate = rules.filter(j => j.Field === "date" && (j.Operator !== "lgt"));
  if (!dateJobs.length) result.time = "unlimited";
  else if (dateJobs.length === 1 && dateJobs[0].Operator === "lgt") {
    const deadline = stamp(dateJobs[0].Value);
    if (Number.isFinite(deadline)) {
      result.timeJob = dateJobs[0];
      result.deadline = deadline;
      result.timeRemainingDays = (deadline - now) / 86400000;
      result.time = deadline <= now ? EXPIRED : deadline - now <= 3 * 86400000 ? "soon" : "limited";
    }
  }
  // Existing quota_total_data uses already weighted metered_data on peers.
  if (!trafficJobs.length) result.traffic = "unlimited";
  else if (trafficJobs.length === 1 && trafficJobs[0].Field === "quota_total_data" &&
    trafficJobs[0].Operator === "lgt") {
    try {
      const data = JSON.parse(trafficJobs[0].Value);
      const limit = Number(data.limit_gb), weight = Number(data.weight);
      const used = Number(peer.metered_data);
      if (Number.isFinite(limit) && limit > 0 && Number.isFinite(weight) &&
        weight >= 0.1 && weight <= 10 && Number.isFinite(used) && used >= 0) {
        result.quotaJob = trafficJobs[0];
        result.quotaLimitGb = limit;
        result.weight = weight;
        result.trafficRemainingGb = limit - used;
        result.traffic = limit <= used ? EXPIRED : limit - used <= 1 ? "soon" : "limited";
      }
    } catch { /* invalid quota stays unknown */ }
  }
  if (otherDate.length) result.time = "unknown";
  return result;
}

export function filterAndSortPeers(peers, {status = "all", query = "", sort = "name"} = {}, now = Date.now()) {
  const needle = String(query || "").trim().toLowerCase();
  const marked = peers.map(peer => ({peer, policy: peerPolicy(peer, now)}));
  const matches = marked.filter(({peer, policy}) => {
    if (needle && ![peer.name, peer.id, peer.allowed_ip].some(x =>
      String(x ?? "").toLowerCase().includes(needle))) return false;
    switch (status) {
      case "expired": return policy.time === EXPIRED || policy.traffic === EXPIRED;
      case "time_expired": return policy.time === EXPIRED;
      case "traffic_expired": return policy.traffic === EXPIRED;
      case "expiring_soon": return policy.time === "soon" || policy.traffic === "soon";
      case "unlimited": return policy.time === "unlimited" && policy.traffic === "unlimited";
      case "unlimited_time": return policy.time === "unlimited";
      case "unlimited_traffic": return policy.traffic === "unlimited";
      case "restricted": return !!peer.restricted;
      case "active": return !peer.restricted && policy.time !== EXPIRED && policy.traffic !== EXPIRED;
      case "unknown": return policy.time === "unknown" || policy.traffic === "unknown";
      default: return true;
    }
  });
  const metric = x => {
    const p = x.policy;
    switch (sort) {
      case "time_remaining": return p.timeRemainingDays ?? Infinity;
      case "traffic_remaining": return p.trafficRemainingGb ?? Infinity;
      case "usage": return -Number(x.peer.metered_data || 0);
      default: return null;
    }
  };
  matches.sort((a, b) => {
    const av = metric(a), bv = metric(b);
    if (av !== null && bv !== null && av !== bv) return av - bv;
    return String(a.peer.name || "").localeCompare(String(b.peer.name || "")) ||
      String(a.peer.id).localeCompare(String(b.peer.id));
  });
  return matches;
}

export function plannedJob(peer, configuration, action, amount, now = new Date(), newId = () => "") {
  const policy = peerPolicy(peer, now.getTime());
  const days = Number(amount);
  if (!Number.isFinite(days) || days <= 0 ||
      (action === "time" && (!Number.isInteger(days) || days > 3650)) ||
      (action === "traffic" && days > 1000000)) {
    throw new Error("Enter a valid positive number (days <= 3650; GB <= 1000000).");
  }
  let existing, value, field;
  if (action === "time") {
    if (policy.time === "unknown") throw new Error("Time rules are ambiguous; edit this peer manually.");
    existing = policy.timeJob;
    const from = Math.max(now.getTime(), policy.deadline || now.getTime());
    const end = new Date(from + days * 86400000);
    // Preserve the server's historical naive-local date field format.
    const digits = n => String(n).padStart(2, "0");
    value = [end.getFullYear(), digits(end.getMonth()+1), digits(end.getDate())].join("-") +
      " " + [digits(end.getHours()), digits(end.getMinutes()), digits(end.getSeconds())].join(":");
    field = "date";
  } else if (action === "traffic") {
    if (policy.traffic === "unknown") throw new Error("Traffic rules are ambiguous; edit this peer manually.");
    existing = policy.quotaJob;
    const base = existing ? policy.quotaLimitGb : Math.max(0, Number(peer.metered_data));
    const limit = base + days;
    if (limit > 1000000) throw new Error("New traffic quota exceeds the server limit.");
    value = JSON.stringify({limit_gb: String(limit), weight: String(existing ? policy.weight : 1)});
    field = "quota_total_data";
  } else throw new Error("Unsupported bulk action");
  return {
    JobID: existing ? existing.JobID : newId(),
    Configuration: configuration, Peer: peer.id, Field: field, Operator: "lgt",
    Value: value, CreationDate: existing?.CreationDate || "",
    ExpireDate: null, Action: "restrict"
  };
}
