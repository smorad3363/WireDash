# UI-only peer Bulk Manager (no backend API changes)

The existing **Select Peers** button opens **Bulk peer manager**. The normal
dashboard remains paginated (50 peers per request); its existing dashboard
and bot endpoints have NOT changed.

The bulk manager explicitly loads the full peer listing using the unchanged
`GET /api/getWireguardConfigurationInfo` only when an administrator opens
it. This is the same endpoint the previous Select Peers modal already used.
Filtering, sorting, paging and checkbox selection happen locally in the
browser. The short **Expired** filter combines active time-expired,
data-exhausted and both-expired peers. A concise badge in each row
distinguishes `Expired · Time`, `Expired · Data` and `Expired · Both`.
The other filters use short labels: All, Expiring soon, Unlimited,
No time limit, No data limit, Restricted, Active, Needs review.
Completion history that is invisible in the legacy API remains in Needs
review; it is NEVER guessed to be a specific expiry cause.

Sorting is done by clicking a **table header** (User, Status, Time,
Data used, Access). The **Data used** header sorts ACTUAL metered usage across
all users (including unlimited users). Its first click sorts highest usage
first, and subsequent clicks reverse direction. This is different from
remaining quota (which is missing for unlimited users); unknown/missing
metering remains last. Time still sorts remaining time, keeping unknown
values last. The separate Sort menu was removed.

The **Data (GB)** column contains ONE compact stacked progress bar with the
`used|quota` number centered on an opaque, high-contrast backing. Download
(blue) and upload (teal) consume proportional segments of this SAME bar
using the existing metered (weighted) GB fields. Hover or accessibility label
reveals upload/download and remaining usage; no additional text row is needed.
Unlimited users show `used|∞` and historical unknown limits show `used|?`.
For these peers WITH recorded traffic, the SAME bar fills with the
blue/teal **download/upload mix of USED traffic**, rather than leaving a
blank bar or implying a fabricated quota percentage. The tooltip says
explicitly that it is a composition chart, NOT quota progress. A genuine
0-GB used peer correctly has an empty bar. Time shows short remaining
days/hours; tooltip includes the exact known expiry when available.

## Default traffic factor and existing peers

Under **Settings → Peers Settings**, an admin can save a preferred default
factor (0.1–10). Because the LEGACY API has no server-shared factor-setting
field, this default uses **local browser storage**. It is NOT synchronized
across administrators/browsers. Saving it does not alter old peer jobs or
the server's stored metering factors. Both peer creation flows (single and
bulk) automatically use this default for NEW users that have a data quota.
The legacy creation contract still requires `traffic_factor=1` for
quota=0/unlimited peers.

To change already-existing peers, select users with **Has data quota**
in Bulk Manager and choose **Set factor**. An individual preview shows
the unchanged GB quota, previous/new factor and projected billed usage.
A separate checkbox explicitly acknowledges that the new factor could
immediately exhaust an existing quota. This calls the existing
`savePeerScheduleJob` endpoint with the same JobID and quota, editing only
the stored `weight`. Missing, expired or legacy/ambiguous active quotas
cannot be changed via that API and are skipped/blocked by preview. Restricted
peers still need the explicit temporary allow-and-restrict consent and
verification already documented below. No automatic action is run just
because an admin saved a default.

Multiple-page selection is maintained in the modal, with distinct
"select page" and "select all matching" controls. Maximum **200 peers
per write operation**.

## Meaning of "unlimited" and expired

The existing peer-list API exposes **active jobs only**. When a time or
traffic rule finishes, the scheduler closes that job and removes it from the
active list. The API does not supply a reliable historic expiry reason for
each restricted peer; manual restrictions are indistinguishable from completed
time/traffic jobs. Consequently a **restricted** peer with no active rule is
shown as **unknown**, not unlimited, and is included in the Unknown/Review
filter. Only an **allowed** peer with no active rule is displayed as "no
active limit". A historical expired user whose rule is closed is *not*
fabricated into the Time expired / Traffic expired filter. Legacy
`total_data`, `total_receive`, `total_sent` and nonstandard time rules
also remain unknown. No filter triggers any access change.

Scheduled dates are stored as timezone-naive server wall times. The manager
defaults to the managed deployment's `Europe/Istanbul` timezone and lets
the admin explicitly enter the server's actual `WGD_TZ`. Date preview and
job values use that server timezone, **never browser local time**, including
across DST boundaries.

## Existing API operations

- Restrict: `POST /api/restrictPeers/<configuration>` with an existing
  `{peers: [...ids]}` payload.
- Allow access: `POST /api/allowAccessPeers/<configuration>`.
- Add time or quota: `POST /api/savePeerScheduleJob` with an existing Job
  object, using existing `date`/`quota_total_data` policy shapes.
  Ambiguous/multiple or legacy policies are rejected in the preview. Time
  extension adds whole days to the later of the SERVER's current wall-clock
  timestamp and any unexpired deadline. Traffic extension grants the selected
  **additional metered GB**: new limit = max(old quota, current metered usage)
  + added GB. It does NOT zero previous usage or silently use an exhausted
  quota as the base. Existing active quota weights are retained. If a quota
  rule has already closed, raw/metered counters infer the persistent weight
  when usage is positive. At zero usage, prior weight cannot be inferred:
  explicit consent for factor 1 is required, or that peer is skipped.
- **Restricted-peer renewal safety:** the existing Save Job endpoint rejects
  restricted peers, and no other old endpoint can save such a job directly.
  The operator must explicitly consent to a short period of temporary access
  (up to 20 selected peers per operation). UI calls old Allow Access, then
  Save Job, then **re-restricts** in a `finally` block, and independently
  verifies that the peer remains restricted. A failed restriction verification
  raises a CRITICAL error and stops further processing. There remains a
  short-lived exposure window while access is enabled. Without accepting this
  trade-off, cancel renewal and use a maintenance procedure. Renewal never
  assumes manual restriction may be removed and **does not auto-unrestrict**.
  A separate explicit Allow Access is required after checking all peer limits.
- Add/remove an existing tag: `POST /api/updateWireguardConfigurationInfo`
  with `Key: "PeerGroups"`. Refreshes current tag memberships before write.
- Delete: `POST /api/deletePeers/<configuration>` after exact
  `DELETE <count>` confirmation.

The UI shows a per-peer new-date/new-quota preview BEFORE any write, then
reloads the current peer list and revalidates IDs, selected access state and
selected job values. Bulk writes are NOT transactional; per-peer failures must
be reviewed. For security the manager aborts a restricted batch when it
cannot verify that original restrictions are restored. A changed policy does
not automatically clear a previously imposed restriction: explicitly Allow
Access only after considering other limits.

No new route, request parameter, authorization rule, backend method, JSON
response field, or bot API behavior is introduced.

## Rollback

The previous published release is pinned at
`backup/pre-bulk-table-ui-2026-10-12`, commit
`e878ddb553281c822103e058305ac5ce35328ddd`, with immutable
Docker image `ghcr.io/smorad3363/wiredash:sha-e878ddb553281c822103e058305ac5ce35328ddd`.
The older pre-Bulk Manager snapshot also remains at
`backup/pre-bulk-actions-2026-10-12`,
`8c05f0abb3b11ee509c44b7c531f667df5626cdb`.
A GitHub source snapshot is **not** a backup of running VPS data. Verify the
existing VPS backup independently before deploying this feature; do not
remove any named Docker volumes. For rollback, select the prior immutable
image in the managed Compose configuration and recreate only the application
container while preserving volumes, then validate the panel, WireGuard
interfaces and persisted peer state. A rollback to an earlier database
snapshot (if ever necessary) is a separate operator-controlled step and
may lose changes since that backup.
