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
