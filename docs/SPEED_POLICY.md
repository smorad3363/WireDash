# Speed policy and renewal history

Settings → Peer Default Settings contains the speed controls. Each account's
settings contains inherit/custom/exempt mode and its reset/renewal history.
The default thresholds are independently 30 GB download and 30 GB upload in
fixed 30-day periods. GB follows existing panel units (1024³ bytes); Mbps means
bits per second. Configure reduced speeds and enable the master control to
activate enforcement. Both controls ship disabled; there are no guessed live
Mbps values. A custom rule can be enabled while the inherited rule is disabled,
provided master control is enabled.

A full successful account usage reset starts a new speed period and removes
its triggered caps, preserving the saved policy. A direction-only reset refreshes
only that direction. Natural period rollover resets only speed accounting,
never paid usage, quota or expiry. Reboot preserves periods and usage. Duration
changes take effect at the next boundary. Threshold/rate edits keep consumption.
Saving global settings asks whether to keep custom rules/exemptions or overwrite
all accounts. Revision checks reject a preview made stale by another edit.

The original bot API is unchanged. New routes under `/api/ui/speed` require a
password-authenticated administrator session, same-origin writes and a CSRF
token. API-key requests, API-key exchanged sessions, client sessions and
installations with authentication disabled cannot configure this feature.

History records factual full/partial resets, expiry changes and API-observed
renewals. A renewal is an API full reset plus an expiry extension within ten
minutes, in either order; payment is not verified. Initial assignments, manual
or scheduled resets, backwards corrections and incomplete restores are distinct.
Old dates without trustworthy evidence remain unknown. Existing log history is
not guessed or imported as confirmed paid renewals. History starts recording
with this release. Reset stages/outbox survive process failure; recovery never
enables or reinstalls an arbitrarily restricted peer.

The original durable metered traffic basis is used. Unknown historic current
periods start from an explicitly incomplete migration baseline rather than
charging lifetime traffic against a monthly threshold. Sampling is approximately
10 seconds; detection is not a byte-exact cutoff. Bytes in an interval crossing
a boundary are apportioned by elapsed time and marked estimated; long gaps are
marked incomplete. At a saturated 1 Gbps link a 10-second interval could permit
about 1.25 GB before detection. Threshold settings should allow for this latency.

Linux TC works on authenticated decrypted peer addresses inside the VPN network
namespace. Download uses WG egress HTB; upload uses an owned IFB. Each peer's
flows share one directional cap. Unclassified traffic has no artificial
whole-interface ceiling. Host queues, GRE, SSH and encrypted shared UDP traffic
are not shaped. Unsupported prefixes, duplicate addresses or foreign queue
ownership produce a visible pending/error state. No VPN peer is enabled by the
speed controller. Master disable removes managed rules on the next sample;
owned empty infrastructure is removed without flushing other queues.

Use the unchanged canonical installation/update command documented in INSTALL.
The immutable image release is promoted only after runtime tests, including
real encrypted TCP/UDP speed and interrupted-install recovery tests. Full
system backups include the additive feature tables. Per-configuration backups
also save an adjacent `.speed.json` file for that configuration's state/history;
restoring one configuration does not change global defaults.
