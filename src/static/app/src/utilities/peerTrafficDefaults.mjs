// UI-only preference (same-origin browser storage): the existing API has NO
// server-wide "default traffic weight" field. Never silently mutate all peers.
// Current peers can be updated by explicit, reviewed bulk quota-job actions.
const KEY = "wiredash.peerTrafficDefaultFactor.v1";
export function validTrafficFactor(value) {
  const n = Number(value);
  return value !== "" && value !== null && value !== undefined &&
    Number.isFinite(n) && n >= 0.1 && n <= 10;
}
export function readTrafficFactor(storage = typeof localStorage === "undefined" ? null : localStorage) {
  try {
    const value = storage?.getItem(KEY);
    return validTrafficFactor(value) ? Number(value) : 1;
  } catch { return 1; }
}
export function saveTrafficFactor(value, storage = typeof localStorage === "undefined" ? null : localStorage) {
  if (!validTrafficFactor(value)) throw new Error("Factor must be between 0.1 and 10.");
  if (!storage) throw new Error("Browser preference storage is unavailable.");
  storage.setItem(KEY, String(Number(value)));
  return Number(value);
}
// A traffic factor has no meaning without a non-zero quota under the existing
// API creation limits (parse_creation_limits explicitly rejects it).
export function factorForCreation(quota, factor = readTrafficFactor()) {
  return Number(quota) > 0 ? factor : 1;
}
