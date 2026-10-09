"""Lightweight wireguard configuration read model shared by production and load tests.

This function does not fetch peers, change interfaces, or write to databases.
"""
def configuration_info_payload(configuration):
    return {
        "configurationInfo": configuration,
        # A peer holds a back-reference to its parent configuration. Sending
        # that once per peer is expensive and also leaks server metadata.
        "configurationPeers": [
            {k: v for k, v in peer.toJson().items() if k != "configuration"}
            for peer in configuration.getPeersList()
        ],
        "configurationRestrictedPeers": [
            {k: v for k, v in peer.toJson().items() if k != "configuration"}
            for peer in configuration.getRestrictedPeersList()
        ]
    }


def ui_configuration_page(configuration, page=1, per_page=50, query="", sort="name", hidden_tags=(), show_all_when_hidden=True):
    """Admin UI only: small paginated response; legacy API stays byte-identical.

    Filtering/sorting always happens before slicing, and summary reflects ALL
    peers. This never writes data or changes the WireGuard config. Per-peer
    metering is calculated using the existing policy engine.
    """
    import heapq
    import ipaddress

    active = configuration.getPeersList()
    restricted = configuration.getRestrictedPeersList()
    all_peers = [(p, False) for p in active] + [(p, True) for p in restricted]
    groups = configuration.configurationInfo.PeerGroups or {}
    ignored_ids = set()
    tagged_ids = set()
    for name, group in groups.items():
        members = set(group.Peers)
        tagged_ids.update(members)
        if name in hidden_tags:
            ignored_ids.update(members)
    text = (query or "").strip()
    matches = []
    total_received = total_sent = 0.0
    active_count = 0
    ranked = []
    for peer, is_restricted in all_peers:
        usage = peer.metered_usage()
        total_received += usage["receive"]
        total_sent += usage["sent"]
        if not is_restricted and peer.status == "running":
            active_count += 1
        if usage["total"] > 0:
            ranked.append((usage["total"], peer.id, peer))
        if peer.id in ignored_ids or (
            not show_all_when_hidden and peer.id not in tagged_ids
        ):
            continue
        if text and not any(text in str(x or "") for x in (
            peer.name, peer.id, peer.allowed_ip
        )):
            continue
        matches.append((peer, is_restricted))

    def address_key(peer):
        try:
            return (0, int(ipaddress.ip_address(str(peer.allowed_ip).split(",")[0].strip().split("/")[0])))
        except (ValueError, AttributeError):
            return (1, 0)

    if sort == "allowed_ip":
        matches.sort(key=lambda item: (address_key(item[0]), item[0].id))
    elif sort == "restricted":
        matches.sort(key=lambda item: (not item[1], item[0].id))
    else:
        if sort not in ("name", "status"):
            sort = "name"
        matches.sort(key=lambda item: (str(getattr(item[0], sort) or ""), item[0].id))

    count = len(matches)
    page = min(max(1, int(page)), max(1, (count + per_page - 1) // per_page))
    shown = matches[(page - 1) * per_page:page * per_page]
    # The historical usage chart only needs top consumers; never serialize
    # thousands of extra peers simply to render a bar chart.
    top = heapq.nlargest(30, ranked, key=lambda x: (x[0], x[1]))
    return {
        "configurationInfo": configuration,
        "configurationPeers": [
            {**{k: v for k, v in peer.toJson().items() if k != "configuration"},
             "restricted": restricted_flag}
            for peer, restricted_flag in shown
        ],
        "chartPeers": [
            {k: v for k, v in peer.toJson().items()
             if k in ("id", "name", "metered_receive", "metered_sent", "metered_data")}
            for _, _, peer in top
        ],
        "totalPeers": len(all_peers),
        "filteredPeers": count,
        "page": page,
        "perPage": per_page,
        "summary": {
            "connectedPeers": active_count,
            "totalUsage": round(total_received + total_sent, 4),
            "totalReceive": round(total_received, 4),
            "totalSent": round(total_sent, 4),
        }
    }
