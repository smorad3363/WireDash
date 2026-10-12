// View helpers for the existing WireDash peer-list payload. No API changes.
// All usage figures are in billed (weighted) GB, matching quota_total_data.
const validUsage = n => typeof n === "number" && Number.isFinite(n) && n >= 0;
const rounded = value => {
  if (!validUsage(value)) return "?";
  const n = Math.round((value + Number.EPSILON) * 10) / 10;
  return String(n);
};
export const formatGb = rounded;

// Expired rule reasons are known only while the ACTIVE limit job is exposed.
// Restricted peers with closed jobs must stay in "Review", not falsely expire
// for a specific cause or appear unlimited.
export function peerStatus(policy) {
  const time = policy?.time === "expired";
  const data = policy?.traffic === "expired";
  if (time && data) return {key:"both", label:"Expired · Both", rank:0};
  if (time) return {key:"time", label:"Expired · Time", rank:1};
  if (data) return {key:"data", label:"Expired · Data", rank:2};
  if (policy?.time === "unknown" || policy?.traffic === "unknown")
    return {key:"review", label:"Needs review", rank:4};
  if (policy?.restricted) return {key:"restricted", label:"Restricted", rank:3};
  if (policy?.time === "soon" || policy?.traffic === "soon")
    return {key:"soon", label:"Expiring soon", rank:5};
  return {key:"active", label:"Active", rank:6};
}

export function peerTimeLabel(policy) {
  if (policy?.time === "expired") return "Expired";
  if (policy?.time === "unlimited") return "No limit";
  if (policy?.time === "unknown") return "Review";
  const days = policy?.timeRemainingDays;
  if (typeof days !== "number" || !Number.isFinite(days)) return "Review";
  if (days <= 0) return "Expired";
  if (days < 1) return `${Math.max(1,Math.ceil(days*24))}h left`;
  return `${Math.ceil(days)}d left`;
}

// Exactly ONE usage bar: centered used|quota label on a readable backdrop.
// The blue/teal segments are download/upload respectively; both represent
// billed traffic (not raw counters) so they reconcile with the quota.
export function peerDataBar(peer, policy) {
  const used = validUsage(peer?.metered_data) ? peer.metered_data : null;
  const limited = typeof policy?.quotaLimitGb === "number" &&
    Number.isFinite(policy.quotaLimitGb) && policy.quotaLimitGb > 0 &&
    (policy.traffic === "limited" || policy.traffic === "soon" ||
      policy.traffic === "expired");
  const quota = limited ? policy.quotaLimitGb : null;
  const noLimit = policy?.traffic === "unlimited";
  const label = `${rounded(used)}|${quota !== null ? rounded(quota) : noLimit ? "∞" : "?"}`;
  const down = validUsage(peer?.metered_sent) ? peer.metered_sent : null;
  const up = validUsage(peer?.metered_receive) ? peer.metered_receive : null;
  // With a quota: filled portion = billed usage / quota.
  // WITHOUT a quota: the bar is a composition chart of already-used traffic,
  // not a fabricated 100%-of-quota progress indicator. All used traffic is
  // represented as blue download / teal upload proportions.
  const occupied = used !== null && used > 0
    ? quota !== null ? Math.min(100, Math.max(0, (used / quota) * 100)) : 100
    : 0;
  let downloadPct = 0, uploadPct = 0, otherPct = occupied;
  if (down !== null && up !== null && down + up > 0 && used !== null && used > 0) {
    const sum = down + up;
    downloadPct = occupied * Math.min(1, down / sum);
    uploadPct = occupied - downloadPct;
    otherPct = 0;
  }
  const unit = "GB";
  const details = [
    `Used: ${rounded(used)} ${unit}`,
    `Quota: ${quota !== null ? rounded(quota) + " " + unit : noLimit ? "No limit" : "Unknown"}`,
    `Download: ${rounded(down)} ${unit} (blue)`,
    `Upload: ${rounded(up)} ${unit} (teal)`
  ];
  if (quota !== null && used !== null)
    details.push(`Remaining: ${rounded(Math.max(0,quota-used))} ${unit}`);
  else if (occupied > 0)
    details.push("Bar shows download/upload mix, NOT percentage of a quota");
  return {
    label, details:details.join(" · "),
    used, quota, hasQuota: quota !== null && used !== null,
    downloadPct, uploadPct, otherPct,
    state: quota !== null ? (used !== null && used >= quota ? "exhausted" :
      used !== null && used / quota >= 0.9 ? "warning" : "limited") :
      noLimit ? "unlimited" : "unknown",
    percent: occupied
  };
}
