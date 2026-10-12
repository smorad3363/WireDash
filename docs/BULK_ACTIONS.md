# UI-only peer Bulk Manager (no backend API changes)

The existing **Select Peers** button opens **Bulk peer manager**. The normal
dashboard remains paginated (50 peers per request); its existing dashboard
and bot endpoints have NOT changed.

The bulk manager explicitly loads the full peer listing using the unchanged
`GET /api/getWireguardConfigurationInfo` only when an administrator opens
it. This is the same endpoint the previous Select Peers modal already used.
Filtering, sorting, paging and checkbox selection happen locally in the
browser. The manager supports separate filters for expired time, exhausted
traffic, expiring soon (3 days or 1 GB), unknown/legacy policy, restricted
access, and time/traffic rules with no active limits. Multiple-page selection
is maintained in the modal, with distinct "select page" and "select all
matching" controls. Maximum **200 peers per write operation**.

## Meaning of "unlimited" and expired

"No active limit" means no **active** matching restriction job appears in
the unchanged API response. The backend can expire/remove jobs after enforcing
them, and an old restricted peer may no longer have its historical job in the
current response. Such a peer can appear to have no active limit but remain
restricted. Older custom `total_data`, `total_receive`, `total_sent` or
nonstandard time rules show "unknown" rather than an invented quota. The
browser interprets the server's timezone-naive scheduled dates in the browser
timezone, so the result is a management aid, not a substitute for reviewing
specific peer jobs. No action is triggered automatically from a status filter.

## Existing API operations

- Restrict: `POST /api/restrictPeers/<configuration>` with an existing
  `{peers: [...ids]}` payload.
- Allow access: `POST /api/allowAccessPeers/<configuration>`.
- Add time or quota: `POST /api/savePeerScheduleJob` with an existing Job
  object, respecting existing quota weight and the `date`/`quota_total_data`
  policy shapes. Ambiguous/multiple policies are skipped. Time extension adds
  days to the later of now and the current future expiry; adding traffic
  increases the existing quota or creates a first quota starting from
  consumed usage.
- Add/remove an existing tag: `POST /api/updateWireguardConfigurationInfo`
  with `Key: "PeerGroups"`. Refreshes current tag memberships before write.
- Delete: `POST /api/deletePeers/<configuration>` after exact
  `DELETE <count>` confirmation.

Before writing, the UI reloads the current peer list and validates the
selected identities. A per-peer status summary shows successful and failed
writes. Bulk jobs are **not transactional**, so partially successful actions
must be reviewed. Extending a policy does not automatically undo a previous
restriction; select the correct peers and explicitly allow access if needed.

No new route, request parameter, authorization rule, backend method, JSON
response field, or bot API behavior is introduced.

## Rollback

The pre-change GitHub branch
`backup/pre-bulk-actions-2026-10-12` pins immutable source commit
`8c05f0abb3b11ee509c44b7c531f667df5626cdb`.
The corresponding immutable Docker image is
`ghcr.io/smorad3363/wiredash:sha-8c05f0abb3b11ee509c44b7c531f667df5626cdb`.
A GitHub source snapshot is **not** a backup of running VPS data. Verify the
existing VPS backup independently before deploying this feature; do not
remove any named Docker volumes. For rollback, select the prior immutable
image in the managed Compose configuration and recreate only the application
container while preserving volumes, then validate the panel, WireGuard
interfaces and persisted peer state. A rollback to an earlier database
snapshot (if ever necessary) is a separate operator-controlled step and
may lose changes since that backup.
