// Existing-API-only renewal for restricted peers. The old schedule-job API
// rejects restricted peers; short temporary access is unavoidable if the
// backend/API contracts must remain untouched. Call only with explicit consent.
// DO NOT infer that a historic restriction was caused by the expired limit.
const restrictedFromResponse = (response, id) =>
  !!response?.status &&
  Array.isArray(response?.data?.configurationRestrictedPeers) &&
  response.data.configurationRestrictedPeers.some(peer => peer.id === id);

export async function renewRestrictedWithRestore({
  id, configuration, job, consent, post, get, onRestoring = () => {}
}) {
  if (!consent) return {saved:false, restored:true, error:"Temporary access consent required"};
  let attemptedAllow = false, saved = false, restored = false, error = "";
  const restrict = () => post(`/api/restrictPeers/${configuration}`, {peers:[id]});
  try {
    // Set the flag BEFORE the request: a failed/aborted HTTP response does
    // not prove the server failed to grant access.
    attemptedAllow = true;
    const allowed = await post(`/api/allowAccessPeers/${configuration}`, {peers:[id]});
    if (!allowed?.status) throw new Error(allowed?.message || "Could not temporarily allow access");
    const response = await post("/api/savePeerScheduleJob", {Job:job});
    if (!response?.status) throw new Error(response?.message || "Could not save policy job");
    saved = true;
  } catch (e) {
    error = e?.message || "Renewal request failed";
  } finally {
    if (attemptedAllow) {
      onRestoring();
      // Restoration on EVERY path, including a request that reported failure.
      // If a status read is unavailable, attempt restriction anyway.
      try {
        let current = null;
        try { current = await get(); } catch { /* proceed fail-closed */ }
        if (!restrictedFromResponse(current,id)) {
          try { await restrict(); } catch { /* verify next */ }
        }
        let verified = null;
        try { verified = await get(); } catch { /* treat unknown as unsafe */ }
        restored = restrictedFromResponse(verified,id);
      } catch { restored = false; }
    }
  }
  if (!restored) {
    error = "CRITICAL: original restriction could not be verified. Inspect peer immediately.";
  }
  return {saved,restored,error};
}
