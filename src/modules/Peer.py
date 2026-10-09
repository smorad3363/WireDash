"""
Peer
"""
import base64
import datetime
import json
import os, subprocess, uuid, random, re
from datetime import timedelta

import jinja2
import sqlalchemy as db
from .PeerJob import PeerJob
from  flask import current_app
from .PeerShareLink import PeerShareLink
from .PeerLimits import metered_usage
from .Utilities import GenerateWireguardPublicKey, CheckAddress, ValidateDNSAddress


class Peer:
    def __init__(self, tableData, configuration):
        self.configuration = configuration
        self.id = tableData["id"]
        self.private_key = tableData["private_key"]
        self.DNS = tableData["DNS"]
        self.endpoint_allowed_ip = tableData["endpoint_allowed_ip"]
        self.name = tableData["name"]
        self.total_receive = tableData["total_receive"]
        self.total_sent = tableData["total_sent"]
        self.total_data = tableData["total_data"]
        self.endpoint = tableData["endpoint"]
        self.status = tableData["status"]
        self.latest_handshake = tableData["latest_handshake"]
        self.allowed_ip = tableData["allowed_ip"]
        self.cumu_receive = tableData["cumu_receive"]
        self.cumu_sent = tableData["cumu_sent"]
        self.cumu_data = tableData["cumu_data"]
        self.mtu = tableData["mtu"]
        self.keepalive = tableData["keepalive"]
        self.notes = tableData.get("notes", "")
        self.remote_endpoint = tableData["remote_endpoint"]
        self.preshared_key = tableData["preshared_key"]
        self.jobs: list[PeerJob] = []
        self.ShareLink: list[PeerShareLink] = []
        self.getJobs()
        self.getShareLink()

    def refreshFromRow(self, row):
        """Refresh counters/metadata in place without re-querying peer jobs or links.

        UI refresh and 10-second background polls read the same database rows.
        Job and share-link associations are maintained by their own write paths.
        """
        for field in (
            "private_key", "DNS", "endpoint_allowed_ip", "name",
            "total_receive", "total_sent", "total_data", "endpoint",
            "status", "latest_handshake", "allowed_ip", "cumu_receive",
            "cumu_sent", "cumu_data", "mtu", "keepalive", "notes",
            "remote_endpoint", "preshared_key",
        ):
            if field in row:
                setattr(self, field, row[field])
        # Link writes refresh associations; expired links should not linger.
        if self.ShareLink:
            now = datetime.now()
            self.ShareLink = [link for link in self.ShareLink if link.ExpireDate > now]

    def metered_usage(self):
        weight = self.configuration.AllPeerJobs.weight_for(self.configuration.Name, self.id)
        return metered_usage(
            (self.total_receive or 0) + (self.cumu_receive or 0),
            (self.total_sent or 0) + (self.cumu_sent or 0), weight
        )

    def toJson(self):
        usage = self.metered_usage()
        # Keep raw VPN counters intact; serialize billed values separately.
        # Do not serialize the weight into API/portal logs.
        return {**self.__dict__,
                "metered_receive": usage["receive"],
                "metered_sent": usage["sent"],
                "metered_data": usage["total"]}

    def __repr__(self):
        return str(self.toJson())

    def updatePeer(self, name: str,
                   private_key: str,
                   preshared_key: str,
                   dns_addresses: str,
                   allowed_ip: str,
                   endpoint_allowed_ip: str,
                   mtu: int,
                   keepalive: int,
                   notes: str
                   ) -> tuple[bool, str | None]:

        if not self.configuration.getStatus():
            self.configuration.toggleConfiguration()

        # Before we do any compute, let us check if the given endpoint allowed ip is valid at all
        if not CheckAddress(endpoint_allowed_ip):
            return False, f"Endpoint Allowed IPs format is incorrect"

        peers = []
        for peer in self.configuration.getPeersList():
            # Make sure to exclude your own data when updating since its not really relevant
            if peer.id == self.id:
                continue
            peers.append(peer)

        used_allowed_ips = []
        for peer in peers:
            ips = peer.allowed_ip.split(',')
            ips = [ip.strip() for ip in ips]
            used_allowed_ips.append(ips)

        if allowed_ip in used_allowed_ips:
            return False, "Allowed IP already taken by another peer"

        if not ValidateDNSAddress(dns_addresses):
            return False, f"DNS IP-Address or FQDN is incorrect"

        if isinstance(mtu, str):
            mtu = 0

        if isinstance(keepalive, str):
            keepalive = 0

        if mtu not in range(0, 1461):
            return False, "MTU format is not correct"

        if keepalive < 0:
            return False, "Persistent Keepalive format is not correct"

        if len(private_key) > 0:
            pubKey = GenerateWireguardPublicKey(private_key)
            if not pubKey[0] or pubKey[1] != self.id:
                return False, "Private key does not match with the public key"

        try:
            rand = random.Random()
            uid = str(uuid.UUID(int=rand.getrandbits(128), version=4))
            psk_exist = len(preshared_key) > 0

            if psk_exist:
                with open(uid, "w+") as f:
                    f.write(preshared_key)

            newAllowedIPs = allowed_ip.replace(" ", "")
            if not CheckAddress(newAllowedIPs):
                    return False, "Allowed IPs entry format is incorrect"

            command = [self.configuration.Protocol, "set", self.configuration.Name, "peer", self.id, "allowed-ips", newAllowedIPs, "preshared-key", uid if psk_exist else "/dev/null"]
            updateAllowedIp = subprocess.check_output(command, stderr=subprocess.STDOUT)

            if psk_exist: os.remove(uid)

            if len(updateAllowedIp.decode().strip("\n")) != 0:
                current_app.logger.error("Update peer failed when updating Allowed IPs")
                return False, "Internal server error"

            command = [f"{self.configuration.Protocol}-quick", "save", self.configuration.Name]
            saveConfig = subprocess.check_output(command, stderr=subprocess.STDOUT)

            if f"wg showconf {self.configuration.Name}" not in saveConfig.decode().strip('\n'):
                current_app.logger.error("Update peer failed when saving the configuration")
                return False, "Internal server error"

            with self.configuration.engine.begin() as conn:
                conn.execute(
                    self.configuration.peersTable.update().values({
                        "name": name,
                        "private_key": private_key,
                        "DNS": dns_addresses,
                        "endpoint_allowed_ip": endpoint_allowed_ip,
                        "mtu": mtu,
                        "keepalive": keepalive,
                        "notes": notes,
                        "preshared_key": preshared_key
                    }).where(
                        self.configuration.peersTable.c.id == self.id
                    )
                )
            return True, None
        except subprocess.CalledProcessError as exc:
            current_app.logger.error(f"Subprocess call failed:\n{exc.output.decode('UTF-8')}")
            return False, "Internal server error"

    def downloadPeer(self) -> dict[str, str]:
        final = {
            "fileName": "",
            "file": ""
        }
        filename = self.name
        if len(filename) == 0:
            filename = "UntitledPeer"
        filename = "".join(filename.split(' '))

        # use previous filtering code if code below is insufficient or faulty
        filename = re.sub(r'[.,/?<>\\:*|"]', '', filename).rstrip(". ") # remove special characters

        reserved_pattern = r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?$" # match com1-9, lpt1-9, con, nul, prn, aux, nul

        if re.match(reserved_pattern, filename, re.IGNORECASE):
            filename = f"file_{filename}" # prepend "file_" if it matches

        for i in filename:
            if re.match("^[a-zA-Z0-9_=+.-]$", i):
                final["fileName"] += i

        interfaceSection = {
            "PrivateKey": self.private_key,
            "Address": self.allowed_ip,
            "MTU": (
                self.configuration.configurationInfo.OverridePeerSettings.MTU
                    if self.configuration.configurationInfo.OverridePeerSettings.MTU else self.mtu
            ),
            "DNS": (
                self.configuration.configurationInfo.OverridePeerSettings.DNS 
                    if self.configuration.configurationInfo.OverridePeerSettings.DNS else self.DNS
            )
        }

        if self.configuration.Protocol == "awg":
            interfaceSection.update({
                "Jc": self.configuration.Jc,
                "Jmin": self.configuration.Jmin,
                "Jmax": self.configuration.Jmax,
                "S1": self.configuration.S1,
                "S2": self.configuration.S2,
                "S3": self.configuration.S3,
                "S4": self.configuration.S4,
                "H1": self.configuration.H1,
                "H2": self.configuration.H2,
                "H3": self.configuration.H3,
                "H4": self.configuration.H4,
                "I1": self.configuration.I1,
                "I2": self.configuration.I2,
                "I3": self.configuration.I3,
                "I4": self.configuration.I4,
                "I5": self.configuration.I5
            })

        peerSection = {
            "PublicKey": self.configuration.PublicKey,
            "AllowedIPs": (
                self.configuration.configurationInfo.OverridePeerSettings.EndpointAllowedIPs
                    if self.configuration.configurationInfo.OverridePeerSettings.EndpointAllowedIPs else self.endpoint_allowed_ip
            ),
            "Endpoint": f'{(self.configuration.configurationInfo.OverridePeerSettings.PeerRemoteEndpoint if self.configuration.configurationInfo.OverridePeerSettings.PeerRemoteEndpoint else self.configuration.DashboardConfig.GetConfig("Peers", "remote_endpoint")[1])}:{(self.configuration.configurationInfo.OverridePeerSettings.ListenPort if self.configuration.configurationInfo.OverridePeerSettings.ListenPort else self.configuration.ListenPort)}',
            "PersistentKeepalive": (
                self.configuration.configurationInfo.OverridePeerSettings.PersistentKeepalive 
                if self.configuration.configurationInfo.OverridePeerSettings.PersistentKeepalive
                else self.keepalive
            ),
            "PresharedKey": self.preshared_key
        }
        combine = [interfaceSection.items(), peerSection.items()]
        for s in range(len(combine)):
            if s == 0:
                final["file"] += "[Interface]\n"
            else:
                final["file"] += "\n[Peer]\n"
            for (key, val) in combine[s]:
                if val is not None and ((type(val) is str and len(val) > 0) or (type(val) is int and val > 0)):
                    final["file"] += f"{key} = {val}\n"

        final["file"] = jinja2.Template(final["file"]).render(configuration=self.configuration)


        if self.configuration.Protocol == "awg":
            final["amneziaVPN"] = json.dumps({
                "containers": [{
                    "awg": {
                        "isThirdPartyConfig": True,
                        "last_config": final['file'],
                        "port": self.configuration.ListenPort,
                        "transport_proto": "udp"
                    },
                    "container": "amnezia-awg"
                }],
                "defaultContainer": "amnezia-awg",
                "description": self.name,
                "hostName": (
                    self.configuration.configurationInfo.OverridePeerSettings.PeerRemoteEndpoint 
                        if self.configuration.configurationInfo.OverridePeerSettings.PeerRemoteEndpoint 
                        else self.configuration.DashboardConfig.GetConfig("Peers", "remote_endpoint")[1])
            })
        return final

    def getJobs(self):
        self.jobs = self.configuration.AllPeerJobs.searchJob(self.configuration.Name, self.id)

    def getShareLink(self):
        self.ShareLink = self.configuration.AllPeerShareLinks.getLink(self.configuration.Name, self.id)

    def resetDataUsage(self, mode: str):
        try:
            with self.configuration.engine.begin() as conn:
                if mode == "total":
                    conn.execute(
                        self.configuration.peersTable.update().values({
                            "total_data": 0,
                            "cumu_data": 0,
                            "total_receive": 0,
                            "cumu_receive": 0,
                            "total_sent": 0,
                            "cumu_sent": 0
                        }).where(
                            self.configuration.peersTable.c.id == self.id
                        )
                    )
                    self.total_data = 0
                    self.total_receive = 0
                    self.total_sent = 0
                    self.cumu_data = 0
                    self.cumu_sent = 0
                    self.cumu_receive = 0
                elif mode == "receive":
                    conn.execute(
                        self.configuration.peersTable.update().values({
                            "total_receive": 0,
                            "cumu_receive": 0,
                        }).where(
                            self.configuration.peersTable.c.id == self.id
                        )
                    )
                    self.cumu_receive = 0
                    self.total_receive = 0
                elif mode == "sent":
                    conn.execute(
                        self.configuration.peersTable.update().values({
                            "total_sent": 0,
                            "cumu_sent": 0
                        }).where(
                            self.configuration.peersTable.c.id == self.id
                        )
                    )
                    self.cumu_sent = 0
                    self.total_sent = 0
                else:
                    return False
        except Exception as e:
            print(e)
            return False
        return True
    
    def getEndpoints(self):
        result = []
        with self.configuration.engine.connect() as conn:
            result = conn.execute(
                db.select(
                    self.configuration.peersHistoryEndpointTable.c.endpoint
                ).group_by(
                    self.configuration.peersHistoryEndpointTable.c.endpoint
                ).where(
                    self.configuration.peersHistoryEndpointTable.c.id == self.id
                )
            ).mappings().fetchall()
        return list(result)
    
    def getTraffics(self, interval: int = 30, startDate: datetime.datetime = None, endDate: datetime.datetime = None):
        if startDate is None and endDate is None:
            endDate = datetime.datetime.now()
            startDate = endDate - timedelta(minutes=interval)
        else:
            endDate = endDate.replace(hour=23, minute=59, second=59, microsecond=999999)
            startDate = startDate.replace(hour=0, minute=0, second=0, microsecond=0)

        with self.configuration.engine.connect() as conn:
            result = conn.execute(
                db.select(
                    self.configuration.peersTransferTable.c.cumu_data,
                    self.configuration.peersTransferTable.c.total_data,
                    self.configuration.peersTransferTable.c.cumu_receive,
                    self.configuration.peersTransferTable.c.total_receive,
                    self.configuration.peersTransferTable.c.cumu_sent,
                    self.configuration.peersTransferTable.c.total_sent,
                    self.configuration.peersTransferTable.c.time
                ).where(
                    db.and_(
                        self.configuration.peersTransferTable.c.id == self.id,
                        self.configuration.peersTransferTable.c.time <= endDate,
                        self.configuration.peersTransferTable.c.time >= startDate,
                        )
                ).order_by(
                    self.configuration.peersTransferTable.c.time
                )
            ).mappings().fetchall()
        # Weight only the returned history; never mutate the transfer database.
        weight = float(self.configuration.AllPeerJobs.weight_for(self.configuration.Name, self.id))
        weighted_history = []
        for item in result:
            row = dict(item)
            for key in ("cumu_receive", "total_receive", "cumu_sent", "total_sent"):
                row[key] = float(row[key] or 0) * weight
            row["cumu_data"] = row["cumu_receive"] + row["cumu_sent"]
            row["total_data"] = row["total_receive"] + row["total_sent"]
            weighted_history.append(row)
        return weighted_history
            
    
    def getSessions(self, startDate: datetime.datetime = None, endDate: datetime.datetime = None):
        if endDate is None:
            endDate = datetime.datetime.now()
        
        if startDate is None:
            startDate = endDate

        endDate = endDate.replace(hour=23, minute=59, second=59, microsecond=999999)
        startDate = startDate.replace(hour=0, minute=0, second=0, microsecond=0)
            

        with self.configuration.engine.connect() as conn:
            result = conn.execute(
                db.select(
                    self.configuration.peersTransferTable.c.time
                ).where(
                    db.and_(
                        self.configuration.peersTransferTable.c.id == self.id,
                        self.configuration.peersTransferTable.c.time <= endDate,
                        self.configuration.peersTransferTable.c.time >= startDate,
                    )
                ).order_by(
                    self.configuration.peersTransferTable.c.time
                )
            ).fetchall()
        time = list(map(lambda x : x[0], result))
        return time
    
    def __duration(self, t1: datetime.datetime, t2: datetime.datetime):
        delta = t1 - t2
        
        hours, remainder = divmod(delta.total_seconds(), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{int(hours):02}:{int(minutes):02}:{int(seconds):02}"
