"""
WireGuard Configuration
"""
from .SafeDatabaseEngine import create_engine as safe_create_engine

from typing import Any

import jinja2
import sqlalchemy, random, shutil, configparser, ipaddress, os, subprocess, time, re, uuid, psutil, traceback
from zipfile import ZipFile
from datetime import datetime, timedelta
from itertools import islice
from flask import current_app

from .DatabaseConnection import ConnectionString
from .DashboardConfig import DashboardConfig
from .Peer import Peer
from .PeerState import (locked_peer_state, address_networks, addresses_overlap,
                        preferred_peer_record)
from .PeerJobs import PeerJobs
from .PeerShareLinks import PeerShareLinks
from .Utilities import StringToBoolean, \
    GenerateWireguardPublicKey, \
    RegexMatch, \
    ValidateDNSAddress, \
    ValidateEndpointAllowedIPs, \
    CheckAddress, \
    CheckPeerKey
from .WireguardConfigurationInfo import WireguardConfigurationInfo, PeerGroupsClass
from .DashboardWebHooks import DashboardWebHooks


class WireguardConfiguration:
    class InvalidConfigurationFileException(Exception):
        def __init__(self, m):
            self.message = m

        def __str__(self):
            return self.message

    def __init__(self, DashboardConfig: DashboardConfig, 
                 AllPeerJobs: PeerJobs,
                 AllPeerShareLinks: PeerShareLinks,
                 DashboardWebHooks: DashboardWebHooks,
                 name: str = None,
                 data: dict = None,
                 backup: dict = None,
                 startup: bool = False,
                 wg: bool = True
                 ):
        self.Peers = []
        self.__parser: configparser.ConfigParser = configparser.RawConfigParser(strict=False)
        self.__parser.optionxform = str
        self.__configFileModifiedTime = None
        self.Status: bool = False
        self.Name: str = ""
        self.PrivateKey: str = ""
        self.PublicKey: str = ""
        self.ListenPort: str = ""
        self.Address: str = ""
        self.DNS: str = ""
        self.Table: str = ""
        self.MTU: str = ""
        self.PreUp: str = ""
        self.PostUp: str = ""
        self.PreDown: str = ""
        self.PostDown: str = ""
        self.SaveConfig: bool = True
        self.Name = name
        self.Protocol = "wg" if wg else "awg"
        self.AllPeerJobs = AllPeerJobs
        self.DashboardConfig = DashboardConfig
        self.DashboardConfig.EnsureDatabaseIntegrity({self.Name: self})
        self.AllPeerShareLinks = AllPeerShareLinks
        self.DashboardWebHooks = DashboardWebHooks
        self.configPath = os.path.join(self.__getProtocolPath(), f'{self.Name}.conf')
        self.engine: sqlalchemy.Engine = safe_create_engine(ConnectionString("wgdashboard"))
        self.metadata: sqlalchemy.MetaData = sqlalchemy.MetaData()
        self.dbType = self.DashboardConfig.GetConfig("Database", "type")[1]

        if name is not None:
            if data is not None and "Backup" in data.keys():
                db = self.__importDatabase(
                    os.path.join(
                        self.__getProtocolPath(),
                        'WGDashboard_Backup',
                        data["Backup"].replace(".conf", ".sql")), True)
            else:
                self.createDatabase()

            self.__parseConfigurationFile()
            self.__initPeersList()
        else:
            self.Name = data["ConfigurationName"]
            self.configPath = os.path.join(self.__getProtocolPath(), f'{self.Name}.conf')

            for i in dir(self):
                if str(i) in data.keys():
                    if isinstance(getattr(self, i), bool):
                        setattr(self, i, StringToBoolean(data[i]))
                    else:
                        setattr(self, i, str(data[i]))

            self.__parser["Interface"] = {
                "PrivateKey": self.PrivateKey,
                "Address": self.Address,
                "ListenPort": self.ListenPort,
                "PreUp": f"{self.PreUp}",
                "PreDown": f"{self.PreDown}",
                "PostUp": f"{self.PostUp}",
                "PostDown": f"{self.PostDown}",
                "SaveConfig": "true"
            }

            if self.Protocol == 'awg':
                self.__parser["Interface"]["Jc"] = self.Jc
                self.__parser["Interface"]["Jc"] = self.Jc
                self.__parser["Interface"]["Jmin"] = self.Jmin
                self.__parser["Interface"]["Jmax"] = self.Jmax
                self.__parser["Interface"]["S1"] = self.S1
                self.__parser["Interface"]["S2"] = self.S2
                self.__parser["Interface"]["S3"] = self.S3
                self.__parser["Interface"]["S4"] = self.S4
                self.__parser["Interface"]["H1"] = self.H1
                self.__parser["Interface"]["H2"] = self.H2
                self.__parser["Interface"]["H3"] = self.H3
                self.__parser["Interface"]["H4"] = self.H4
                self.__parser["Interface"]["I1"] = self.I1
                self.__parser["Interface"]["I2"] = self.I2
                self.__parser["Interface"]["I3"] = self.I3
                self.__parser["Interface"]["I4"] = self.I4
                self.__parser["Interface"]["I5"] = self.I5

            if "Backup" not in data.keys():
                self.createDatabase()
                with open(self.configPath, "w+") as configFile:
                    self.__parser.write(configFile)
                    current_app.logger.info(f"Configuration file {self.configPath} created")
                self.__initPeersList()

        if not os.path.exists(os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup')):
            os.mkdir(os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup'))

        current_app.logger.info(f"Initialized Configuration: {name}")
        self.__dumpDatabase()
        if self.getAutostartStatus() and not self.getStatus() and startup:
            status, ext = self.toggleConfiguration()
            if not status:
                current_app.logger.error(f"Failed to autostart configuration: {name}. Reason: {ext}")
            else:
                current_app.logger.info(f"Autostart Configuration: {name}")
            
        self.configurationInfo: WireguardConfigurationInfo | None = None
        configurationInfoJson = self.readConfigurationInfo()
        if not configurationInfoJson:
            self.configurationInfo = WireguardConfigurationInfo(**{})
            self.initConfigurationInfo()
        else:
            self.configurationInfo = WireguardConfigurationInfo.model_validate_json(configurationInfoJson.get("Info"))
        
        if self.Status:
            self.addAutostart()

    def __getProtocolPath(self) -> str:
        _, path = self.DashboardConfig.GetConfig("Server", "wg_conf_path") if self.Protocol == "wg" \
            else self.DashboardConfig.GetConfig("Server", "awg_conf_path")
        return path

    def __initPeersList(self):
        self.Peers: list[Peer] = []
        self.getPeers()
        self.getRestrictedPeersList()

    def getRawConfigurationFile(self):
        return open(self.configPath, 'r').read()

    def updateRawConfigurationFile(self, newRawConfiguration):
        backupStatus, backup = self.backupConfigurationFile()
        if not backupStatus:
            return False, "Cannot create backup"

        if self.Status:
            self.toggleConfiguration()

        with open(self.configPath, 'w') as f:
            f.write(newRawConfiguration)

        status, err = self.toggleConfiguration()
        if not status:
            restoreStatus = self.restoreBackup(backup['filename'])
            current_app.logger.error(f"Backup restore status: {restoreStatus}")
            self.toggleConfiguration()
            return False, err
        return True, None

    def __parseConfigurationFile(self):
        with open(self.configPath, 'r') as f:
            original = [l.rstrip("\n") for l in f.readlines()]
            try:
                start = original.index("[Interface]")

                # Clean
                for i in range(start, len(original)):
                    if original[i] == "[Peer]":
                        break
                    split = re.split(r'\s*=\s*', original[i], 1)
                    if len(split) == 2:
                        key = split[0]
                        if key in dir(self):
                            if isinstance(getattr(self, key), bool):
                                setattr(self, key, False)
                            else:
                                setattr(self, key, "")

                # Set
                for i in range(start, len(original)):
                    if original[i] == "[Peer]":
                        break
                    split = re.split(r'\s*=\s*', original[i], 1)
                    if len(split) == 2:
                        key = split[0]
                        value = split[1]
                        if key in dir(self):
                            if isinstance(getattr(self, key), bool):
                                setattr(self, key, StringToBoolean(value))
                            else:
                                if len(getattr(self, key)) > 0:
                                    setattr(self, key, f"{getattr(self, key)}, {value}")
                                else:
                                    setattr(self, key, value)
            except ValueError as e:
                raise self.InvalidConfigurationFileException(
                    "[Interface] section not found in " + self.configPath)
            if self.PrivateKey:
                self.PublicKey = self.__getPublicKey()
            self.Status = self.getStatus()

    def __dropDatabase(self):
        existingTables = [self.Name, f'{self.Name}_restrict_access', f'{self.Name}_transfer', f'{self.Name}_deleted']
        try:
            with self.engine.begin() as conn:
                for t in existingTables:
                    conn.execute(
                        sqlalchemy.text(
                            f'DROP TABLE "{t}"'
                        )
                    )
        except Exception as e:
            current_app.logger.error("Dropping table failed")
            return False
        return True

    def createDatabase(self, dbName = None):
        def generate_column_obj():
            return [
                sqlalchemy.Column('id', sqlalchemy.String(255), nullable=False, primary_key=True),
                sqlalchemy.Column('private_key', sqlalchemy.String(255)),
                sqlalchemy.Column('DNS', sqlalchemy.Text),
                sqlalchemy.Column('endpoint_allowed_ip', sqlalchemy.Text),
                sqlalchemy.Column('name', sqlalchemy.Text),
                sqlalchemy.Column('total_receive', sqlalchemy.Float),
                sqlalchemy.Column('total_sent', sqlalchemy.Float),
                sqlalchemy.Column('total_data', sqlalchemy.Float),
                sqlalchemy.Column('endpoint', sqlalchemy.String(255)),
                sqlalchemy.Column('status', sqlalchemy.String(255)),
                sqlalchemy.Column('latest_handshake', sqlalchemy.String(255)),
                sqlalchemy.Column('allowed_ip', sqlalchemy.String(255)),
                sqlalchemy.Column('cumu_receive', sqlalchemy.Float),
                sqlalchemy.Column('cumu_sent', sqlalchemy.Float),
                sqlalchemy.Column('cumu_data', sqlalchemy.Float),
                sqlalchemy.Column('mtu', sqlalchemy.Integer),
                sqlalchemy.Column('keepalive', sqlalchemy.Integer),
                sqlalchemy.Column('notes', sqlalchemy.Text),
                sqlalchemy.Column('remote_endpoint', sqlalchemy.String(255)),
                sqlalchemy.Column('preshared_key', sqlalchemy.String(255))
            ]

        if dbName is None:
            dbName = self.Name

        self.peersTable = sqlalchemy.Table(
            f'{dbName}', self.metadata, *generate_column_obj(), extend_existing=True
        )

        self.peersRestrictedTable = sqlalchemy.Table(
            f'{dbName}_restrict_access', self.metadata, *generate_column_obj(), extend_existing=True
        )

        self.peersDeletedTable = sqlalchemy.Table(
            f'{dbName}_deleted', self.metadata, *generate_column_obj(), extend_existing=True
        )

        if self.DashboardConfig.GetConfig("Database", "type")[1] == 'sqlite':
            time_col_type = sqlalchemy.DATETIME
        else:
            time_col_type = sqlalchemy.TIMESTAMP

        self.peersTransferTable = sqlalchemy.Table(
            f'{dbName}_transfer', self.metadata,
            sqlalchemy.Column('id', sqlalchemy.String(255), nullable=False),
            sqlalchemy.Column('total_receive', sqlalchemy.Float),
            sqlalchemy.Column('total_sent', sqlalchemy.Float),
            sqlalchemy.Column('total_data', sqlalchemy.Float),
            sqlalchemy.Column('cumu_receive', sqlalchemy.Float),
            sqlalchemy.Column('cumu_sent', sqlalchemy.Float),
            sqlalchemy.Column('cumu_data', sqlalchemy.Float),
            sqlalchemy.Column('time', time_col_type, server_default=sqlalchemy.func.now()),
            extend_existing=True
        )
        
        self.peersHistoryEndpointTable = sqlalchemy.Table(
            f'{dbName}_history_endpoint', self.metadata,
            sqlalchemy.Column('id', sqlalchemy.String(255), nullable=False),
            sqlalchemy.Column('endpoint', sqlalchemy.String(255), nullable=False),
            sqlalchemy.Column('time', time_col_type)
        )
        
        self.infoTable = sqlalchemy.Table(
            'ConfigurationsInfo', self.metadata,
            sqlalchemy.Column('ID', sqlalchemy.String(255), primary_key=True),
            sqlalchemy.Column('Info', sqlalchemy.Text),
            extend_existing=True
        )

        self.metadata.create_all(self.engine)

    def __dumpDatabase(self):
        with self.engine.connect() as conn:
            tables = [self.peersTable, self.peersRestrictedTable, self.peersTransferTable, self.peersDeletedTable]
            for i in tables:
                rows = conn.execute(i.select()).mappings().fetchall()
                for row in rows:
                    insert_stmt = i.insert().values(dict(row))
                    yield str(insert_stmt.compile(compile_kwargs={"literal_binds": True}))

    def __importDatabase(self, sqlFilePath, restore = False) -> bool:
        if not restore:
            self.__dropDatabase()
        self.createDatabase()
        if not os.path.exists(sqlFilePath):
            return False
        with self.engine.begin() as conn:
            with open(sqlFilePath, 'r') as f:
                for l in f.readlines():
                    l = l.rstrip("\n")
                    if len(l) > 0:
                        conn.execute(sqlalchemy.text(l))
        return True

    def __getPublicKey(self) -> str:
        return GenerateWireguardPublicKey(self.PrivateKey)[1]

    def getStatus(self) -> bool:
        self.Status = self.Name in psutil.net_if_addrs().keys()
        return self.Status

    def getAutostartStatus(self):
        s, d = self.DashboardConfig.GetConfig("WireGuardConfiguration", "autostart")
        return self.Name in d
    
    def addAutostart(self):
        s, d = self.DashboardConfig.GetConfig("WireGuardConfiguration", "autostart")
        if self.Name not in d:
            d.append(self.Name)
            self.DashboardConfig.SetConfig("WireGuardConfiguration", "autostart", d)
    
    def removeAutostart(self):
        s, d = self.DashboardConfig.GetConfig("WireGuardConfiguration", "autostart")
        if self.Name in d:
            d.remove(self.Name)
            self.DashboardConfig.SetConfig("WireGuardConfiguration", "autostart", d)

    @locked_peer_state
    def getRestrictedPeers(self):
        with self.engine.connect() as conn:
            restricted = conn.execute(self.peersRestrictedTable.select()).mappings().fetchall()
        cached = {p.id: p for p in getattr(self, "RestrictedPeers", [])}
        peers = []
        for row in restricted:
            peer = cached.get(row["id"])
            if peer is None:
                peer = Peer(row, self)
            else:
                peer.refreshFromRow(row)
            peers.append(peer)
        self.RestrictedPeers = peers

    @locked_peer_state
    def configurationFileChanged(self) :
        mt = os.path.getmtime(self.configPath)
        changed = self.__configFileModifiedTime is None or self.__configFileModifiedTime != mt
        self.__configFileModifiedTime = mt
        return changed

    @locked_peer_state
    def getPeers(self):
        # The DB owns assigned addresses, identity and traffic history. The
        # config file can briefly lag a restrict/delete and wg showconf omits
        # AllowedIPs when another peer has stolen the route. Never replace a
        # valid lease with N/A or resurrect a restricted customer from a file.
        if self.configurationFileChanged():
            sections = []
            current = None
            with open(self.configPath) as config_file:
                for raw in config_file:
                    line = raw.strip()
                    if line == "[Peer]":
                        current = {}
                        sections.append(current)
                    elif line.startswith("["):
                        current = None
                    elif current is not None and "=" in line:
                        key, value = (part.strip() for part in line.split("=", 1))
                        if key == "#Name#":
                            current["name"] = value
                        elif not key.startswith(("#", ";")):
                            current[key] = value
            with self.engine.begin() as conn:
                active = {r["id"]: r for r in conn.execute(
                    self.peersTable.select()).mappings()}
                restricted = {r["id"] for r in conn.execute(
                    self.peersRestrictedTable.select()).mappings()}
                deleted_table = getattr(self, "peersDeletedTable", None)
                deleted = set() if deleted_table is None else {
                    r["id"] for r in conn.execute(deleted_table.select()).mappings()}
                occupied = self._lease_rows(conn)
                for section in sections:
                    key = section.get("PublicKey")
                    address = section.get("AllowedIPs", "")
                    if (not key or key in active or key in restricted or key in deleted
                            or not address_networks(address)):
                        continue
                    if any(row["id"] != key and addresses_overlap(address, row["allowed_ip"])
                           for row in occupied):
                        continue
                    row = {
                        "id": key, "private_key": "",
                        "DNS": self.DashboardConfig.GetConfig("Peers", "peer_global_DNS")[1],
                        "endpoint_allowed_ip": self.DashboardConfig.GetConfig("Peers", "peer_endpoint_allowed_ip")[1],
                        "name": section.get("name", ""),
                        "total_receive": 0, "total_sent": 0, "total_data": 0,
                        "endpoint": "N/A", "status": "stopped", "latest_handshake": "N/A",
                        "allowed_ip": address,
                        "cumu_receive": 0, "cumu_sent": 0, "cumu_data": 0,
                        "mtu": self.DashboardConfig.GetConfig("Peers", "peer_mtu")[1] or None,
                        "keepalive": self.DashboardConfig.GetConfig("Peers", "peer_keep_alive")[1] or None,
                        "notes": "",
                        "remote_endpoint": self.DashboardConfig.GetConfig("Peers", "remote_endpoint")[1],
                        "preshared_key": section.get("PresharedKey", "")}
                    conn.execute(self.peersTable.insert().values(row))
                    occupied.append(row)
                    active[key] = row
        with self.engine.connect() as conn:
            existingPeers = conn.execute(self.peersTable.select()).mappings().fetchall()
        cached = {p.id: p for p in self.Peers}
        peers = []
        peer_type = self._peer_type()
        for row in existingPeers:
            peer = cached.get(row["id"])
            if peer is None:
                peer = peer_type(row, self)
            else:
                peer.refreshFromRow(row)
            peers.append(peer)
        self.Peers = peers

    def _peer_type(self):
        return Peer

    def _lease_rows(self, conn):
        return [dict(row) for table in (self.peersTable, self.peersRestrictedTable)
                for row in conn.execute(table.select()).mappings()]

    @locked_peer_state
    def validatePeerAddresses(self, peers):
        with self.engine.connect() as conn:
            occupied = self._lease_rows(conn)
        staged = []
        for peer in peers:
            address = peer.get("allowed_ip", "")
            networks = address_networks(address)
            if not networks:
                return False, "Allowed IPs entry format is incorrect"
            for server in self.Address.split(","):
                if server.strip():
                    host = ipaddress.ip_interface(server.strip()).ip
                    if any(host.version == n.version and host in n for n in networks):
                        return False, "Allowed IP overlaps the server address"
            if any(row["id"] != peer["id"] and addresses_overlap(address, row["allowed_ip"])
                   for row in occupied + staged):
                return False, "Allowed IP already assigned to another peer"
            staged.append(peer)
        return True, None

    def logPeersTraffic(self):
        # The peer table is the authoritative cumulative traffic ledger.
        # History rows are for charts only, so record at most one sample per
        # peer every five minutes. Record immediately after a panel restart.
        sampled_at = time.monotonic()
        previous = getattr(self, "_traffic_history_logged", {})
        rows = []
        for peer in self.Peers:
            if peer.status != "running":
                continue
            if sampled_at - previous.get(peer.id, float("-inf")) < 300:
                continue
            rows.append({
                "id": peer.id,
                "total_receive": peer.total_receive,
                "total_sent": peer.total_sent,
                "total_data": peer.total_data,
                "cumu_sent": peer.cumu_sent,
                "cumu_receive": peer.cumu_receive,
                "cumu_data": peer.cumu_data,
                "time": datetime.now(),
            })
        if rows:
            with self.engine.begin() as conn:
                conn.execute(self.peersTransferTable.insert(), rows)
            for row in rows:
                previous[row["id"]] = sampled_at
            self._traffic_history_logged = previous
    
    def logPeersHistoryEndpoint(self):
        with self.engine.begin() as conn:
            for tempPeer in self.Peers:
                if tempPeer.status == "running":
                    endpoint = tempPeer.endpoint.rsplit(":", 1)    
                    if len(endpoint) == 2 and len(endpoint[0]) > 0:
                        exist = conn.execute(
                            self.peersHistoryEndpointTable.select().where(
                                sqlalchemy.and_(
                                    self.peersHistoryEndpointTable.c.id == tempPeer.id,
                                    self.peersHistoryEndpointTable.c.endpoint == endpoint[0]
                                )
                            )
                        ).mappings().fetchone()
                        if not exist:
                            conn.execute(
                                self.peersHistoryEndpointTable.insert().values({
                                    "id": tempPeer.id,
                                    "endpoint": endpoint[0],
                                    "time": datetime.now()
                                })
                            )
                          
    @locked_peer_state
    def addPeers(self, peers: list) -> tuple[bool, list, str]:
        valid, error = self.validatePeerAddresses(peers)
        if not valid:
            return False, [], error
        with self.engine.connect() as conn:
            existing = {row["id"] for row in self._lease_rows(conn)}
        keys = [row["id"] for row in peers]
        if len(set(keys)) != len(keys) or any(key in existing for key in keys):
            return False, [], "This peer already exists"
        if any(not CheckPeerKey(key) for key in keys):
            return False, [], "Peer key format is incorrect"
        installed = []
        try:
            with self.engine.begin() as conn:
                for peer in peers:
                    row = dict(peer)
                    for field in ("total_receive", "total_sent", "total_data",
                                  "cumu_receive", "cumu_sent", "cumu_data"):
                        row[field] = 0
                    row.update(endpoint="N/A", status="stopped", latest_handshake="N/A",
                               remote_endpoint=self.DashboardConfig.GetConfig("Peers", "remote_endpoint")[1])
                    conn.execute(self.peersTable.insert().values(row))
                    # Include the key in cleanup even if a command partly succeeds.
                    installed.append(peer["id"])
                    self._set_peer_route(row)
                status, message = self.__wgSave()
                if not status:
                    raise RuntimeError("Could not save new peers")
        except Exception:
            for key in installed:
                try:
                    subprocess.check_output([self.Protocol, "set", self.Name, "peer", key, "remove"],
                                            stderr=subprocess.STDOUT)
                except subprocess.CalledProcessError:
                    current_app.logger.error("Could not remove a partially created peer")
            self.__wgSave()
            current_app.logger.error("Peer provisioning failed; database transaction rolled back")
            return False, [], "Could not create peer; changes rolled back"
        self.getPeers()
        result = [self.searchPeer(key)[1] for key in keys]
        try:
            self.DashboardWebHooks.RunWebHook("peer_created", {"configuration": self.Name, "peers": keys})
        except Exception:
            current_app.logger.error("Peer created; webhook delivery failed")
        return True, result, ""

    def searchPeer(self, publicKey):
        for i in self.Peers:
            if i.id == publicKey:
                return True, i
        return False, None

    @locked_peer_state
    def allowAccessPeers(self, listOfPublicKeys) -> tuple[bool, str]:
        staged = []
        with self.engine.connect() as conn:
            for key in dict.fromkeys(listOfPublicKeys):
                active = conn.execute(self.peersTable.select().where(
                    self.peersTable.c.id == key)).mappings().first()
                restricted = conn.execute(self.peersRestrictedTable.select().where(
                    self.peersRestrictedTable.c.id == key)).mappings().first()
                if active is None and restricted is None:
                    return False, "Peer does not exist"
                row = preferred_peer_record(active, restricted)
                if not CheckPeerKey(row["id"]):
                    return False, "Peer key format is incorrect"
                staged.append(row)
        valid, error = self.validatePeerAddresses(staged)
        if not valid:
            return False, error
        if not self.getStatus():
            self.toggleConfiguration()
        for row in staged:
            # Reapply the real WireGuard route even on retries. A DB row alone
            # does not prove the peer was restored in the kernel.
            self._set_peer_route(row)
            with self.engine.begin() as conn:
                present = conn.execute(self.peersTable.select().where(
                    self.peersTable.c.id == row["id"])).first()
                if present is None:
                    conn.execute(self.peersTable.insert().values(row))
                else:
                    conn.execute(self.peersTable.update().where(
                        self.peersTable.c.id == row["id"]).values(row))
                conn.execute(self.peersRestrictedTable.delete().where(
                    self.peersRestrictedTable.c.id == row["id"]))
        status, message = self.__wgSave()
        if not status:
            return False, message
        self.getPeers()
        self.getRestrictedPeers()
        return True, "Allow access successfully"

    def _set_peer_route(self, row):
        import tempfile
        # A private PSK file is never left behind on a subprocess failure.
        with tempfile.TemporaryDirectory(prefix="wiredash-peer-") as directory:
            psk_path = "/dev/null"
            if row.get("preshared_key"):
                psk_path = os.path.join(directory, "psk")
                fd = os.open(psk_path, os.O_WRONLY | os.O_CREAT, 0o600)
                with os.fdopen(fd, "w") as handle:
                    handle.write(row["preshared_key"])
            subprocess.check_output([
                self.Protocol, "set", self.Name, "peer", row["id"],
                "allowed-ips", row["allowed_ip"].replace(" ", ""),
                "preshared-key", psk_path], stderr=subprocess.STDOUT)

    @locked_peer_state
    def resetPeerData(self, key, mode):
        if mode not in ("total", "receive", "sent"):
            return False, "Invalid usage reset mode"
        found, peer = self.searchPeer(key)
        if not found:
            return False, "Configuration/Peer does not exist"
        valid, error = self.validatePeerAddresses([{"id": key, "allowed_ip": peer.allowed_ip}])
        if not valid:
            return False, error
        # Sample and remove the old kernel counters before clearing the DB.
        # The poller cannot recreate a row between restrict and allow.
        self.getPeersTransfer()
        status, message = self.restrictPeers([key])
        if not status:
            return False, message
        with self.engine.begin() as conn:
            row = conn.execute(self.peersRestrictedTable.select().where(
                self.peersRestrictedTable.c.id == key)).mappings().one()
            conn.execute(self.peersTable.insert().values(dict(row)))
            conn.execute(self.peersRestrictedTable.delete().where(
                self.peersRestrictedTable.c.id == key))
        self.getPeers()
        found, peer = self.searchPeer(key)
        if not peer.resetDataUsage(mode):
            return False, "Could not reset peer usage"
        return self.allowAccessPeers([key])

    @locked_peer_state
    def restrictPeers(self, listOfPublicKeys) -> tuple[bool, str]:
        numOfRestrictedPeers = 0
        numOfFailedToRestrictPeers = 0
        if not self.getStatus():
            self.toggleConfiguration()

        with self.engine.begin() as conn:
            for p in listOfPublicKeys:
                found, pf = self.searchPeer(p)
                if found:
                    try:
                        command = [self.Protocol, "set", self.Name, "peer", pf.id, "remove"]
                        subprocess.check_output(command, stderr=subprocess.STDOUT)

                        # Replace any stale restricted duplicate in the SAME
                        # transaction, using the current active peer record.
                        # This keeps quota-reset and subsequent allow idempotent.
                        active = conn.execute(self.peersTable.select().where(
                            self.peersTable.c.id == pf.id)).mappings().first()
                        restricted = conn.execute(self.peersRestrictedTable.select().where(
                            self.peersRestrictedTable.c.id == pf.id)).mappings().first()
                        record = preferred_peer_record(active, restricted)
                        conn.execute(self.peersRestrictedTable.delete().where(
                            self.peersRestrictedTable.c.id == pf.id))
                        conn.execute(self.peersRestrictedTable.insert().values(record))
                        conn.execute(
                            self.peersRestrictedTable.update().values({
                                "status": "stopped"
                            }).where(
                                self.peersRestrictedTable.columns.id == pf.id
                            )
                        )
                        conn.execute(
                            self.peersTable.delete().where(
                                self.peersTable.columns.id == pf.id
                            )
                        )
                        numOfRestrictedPeers += 1
                    except Exception as e:
                        traceback.print_stack()
                        numOfFailedToRestrictPeers += 1

        if not self.__wgSave()[0]:
            return False, "Failed to save configuration through WireGuard"
        self.getRestrictedPeers()
        self.getPeers()
        if numOfRestrictedPeers == len(listOfPublicKeys):
            return True, f"Restricted {numOfRestrictedPeers} peer(s)"
        return False, f"Restricted {numOfRestrictedPeers} peer(s) successfully. Failed to restrict {numOfFailedToRestrictPeers} peer(s)"


    @locked_peer_state
    def deletePeers(self, listOfPublicKeys, AllPeerJobs: PeerJobs, AllPeerShareLinks: PeerShareLinks) -> tuple[bool, str]:
        """Remove allowed AND already restricted peers via the unchanged API.

        Restricted peers are stored in a separate SQLite table, and are NOT
        present in self.Peers or in the live WireGuard interface. Never enable
        them just to delete them. Validate before dereferencing .jobs; a stale
        or missing selection must return a normal error, not HTTP 500.
        """
        deleted = []
        failed = 0
        active_changed = False
        # Cache the restricted records ONCE for the current small API batch.
        restricted = {peer.id: peer for peer in self.getRestrictedPeersList()}
        for key in listOfPublicKeys:
            allowed, peer = self.searchPeer(key)
            if not allowed:
                peer = restricted.get(key)
            if peer is None:
                failed += 1
                continue
            try:
                # Do not alter the WireGuard interface for restricted users:
                # they are already absent. Avoid bringing up a stopped VPN if
                # the selected users are all restricted.
                if allowed:
                    if not self.getStatus():
                        self.toggleConfiguration()
                    subprocess.check_output(
                        [self.Protocol, "set", self.Name, "peer", peer.id, "remove"],
                        stderr=subprocess.STDOUT)
                table = self.peersTable if allowed else self.peersRestrictedTable
                with self.engine.begin() as conn:
                    removed = conn.execute(
                        table.delete().where(table.c.id == peer.id))
                    if removed.rowcount != 1:
                        raise RuntimeError("Selected peer is no longer in the expected table")
                    other = self.peersRestrictedTable if allowed else self.peersTable
                    conn.execute(other.delete().where(other.c.id == peer.id))
                if allowed:
                    active_changed = True
                # Policy/share cleanup happens AFTER successful peer deletion.
                # A deleted, restricted user is no longer in searchPeer().
                for job in peer.jobs:
                    AllPeerJobs.deleteJob(job)
                for share in peer.ShareLink:
                    AllPeerShareLinks.updateLinkExpireDate(share.ShareID, datetime.now())
                deleted.append(peer.id)
            except Exception:
                current_app.logger.exception("Could not delete selected peer")
                failed += 1
        # Save WireGuard only if a live peer was actually removed.
        if active_changed and not self.__wgSave()[0]:
            return False, "Could not save WireGuard configuration after deletion"
        if deleted:
            AllPeerJobs.forget_weights(self.Name, deleted)
        self.getPeers()
        self.getRestrictedPeers()
        if not deleted and not failed:
            return False, "No peer(s) selected"
        if failed:
            return False, f"Deleted {len(deleted)} peer(s); failed to delete {failed} peer(s). Refresh list before retry."
        self.DashboardWebHooks.RunWebHook("peer_deleted", {
            "configuration": self.Name,
            "peers": deleted
        })
        return True, f"Deleted {len(deleted)} peer(s)"

    def __wgSave(self) -> tuple[bool, str] | tuple[bool, None]:
        try:
            command = [f"{self.Protocol}-quick", "save", self.Name]
            subprocess.check_output(command, stderr=subprocess.STDOUT)

            return True, None
        except subprocess.CalledProcessError as e:
            current_app.logger.error(f"Failed to process command:\n{str(e)}")
            return False, "Internal server error"

    @locked_peer_state
    def getPeersLatestHandshake(self):
        if not self.getStatus():
            self.toggleConfiguration()
        try:
            command = [self.Protocol, "show", self.Name, "latest-handshakes"]
            latestHandshake = subprocess.check_output(command, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError:
            return "stopped"
        latestHandshake = latestHandshake.decode("UTF-8").split()
        count = 0
        now = datetime.now()
        time_delta = timedelta(minutes=3)

        with self.engine.begin() as conn:
            for _ in range(int(len(latestHandshake) / 2)):
                minus = now - datetime.fromtimestamp(int(latestHandshake[count + 1]))
                if minus < time_delta:
                    status = "running"
                else:
                    status = "stopped"
                if int(latestHandshake[count + 1]) > 0:
                    conn.execute(
                        self.peersTable.update().values({
                            "latest_handshake": str(minus).split(".", maxsplit=1)[0],
                            "status": status
                        }).where(
                            self.peersTable.columns.id == latestHandshake[count]
                        )
                    )
                else:
                    conn.execute(
                        self.peersTable.update().values({
                            "latest_handshake": "No Handshake",
                            "status": status
                        }).where(
                            self.peersTable.columns.id == latestHandshake[count]
                        )
                    )
                count += 2

    @locked_peer_state
    def getPeersTransfer(self):
        if not self.getStatus():
            self.toggleConfiguration()
        # try:
        command = [self.Protocol, "show", self.Name, "transfer"]
        data_usage = subprocess.check_output(command, stderr=subprocess.STDOUT)

        data_usage = data_usage.decode("UTF-8").split("\n")
        
        data_usage = [p.split("\t") for p in data_usage]
        cur_i = None
        with self.engine.begin() as conn:
            for i in range(len(data_usage)):
                if len(data_usage[i]) == 3:
                    cur_i = conn.execute(
                        self.peersTable.select().where(
                            self.peersTable.c.id == data_usage[i][0]
                        )
                    ).mappings().fetchone()
                    if cur_i is not None:
                        total_sent = cur_i['total_sent']
                        total_receive = cur_i['total_receive']
                        cur_total_sent = float(data_usage[i][2]) / (1024 ** 3)
                        cur_total_receive = float(data_usage[i][1]) / (1024 ** 3)
                        cumulative_receive = cur_i['cumu_receive'] + total_receive
                        cumulative_sent = cur_i['cumu_sent'] + total_sent
                        if (total_sent * 0.999 ) <= cur_total_sent and (total_receive * 0.999) <= cur_total_receive: # An accuracy of 1K ppm is sufficient
                            total_sent = cur_total_sent
                            total_receive = cur_total_receive
                            billed_cumulative_receive = cur_i['cumu_receive']
                            billed_cumulative_sent = cur_i['cumu_sent']
                        else:
                            billed_cumulative_receive = cumulative_receive
                            billed_cumulative_sent = cumulative_sent
                            conn.execute(
                                self.peersTable.update().values({
                                    "cumu_receive": cumulative_receive,
                                    "cumu_sent": cumulative_sent,
                                    "cumu_data": cumulative_sent + cumulative_receive
                                }).where(
                                    self.peersTable.c.id == data_usage[i][0]
                                )
                            )

                            total_sent = 0
                            total_receive = 0
                        status, p = self.searchPeer(data_usage[i][0])
                        if status:
                            if p.total_receive != total_receive or p.total_sent != total_sent:
                                conn.execute(
                                    self.peersTable.update().values({
                                        "total_receive": total_receive,
                                        "total_sent": total_sent,
                                        "total_data": total_receive + total_sent
                                    }).where(
                                        self.peersTable.c.id == data_usage[i][0]
                                    )
                                )
                            # The transfer DB is authoritative. Keep the cached Peer
                            # model aligned so weighted API/UI values advance live.
                            p.total_receive = total_receive
                            p.total_sent = total_sent
                            p.total_data = total_receive + total_sent
                            p.cumu_receive = billed_cumulative_receive
                            p.cumu_sent = billed_cumulative_sent
                            p.cumu_data = billed_cumulative_receive + billed_cumulative_sent

            

    @locked_peer_state
    def getPeersEndpoint(self):
        if not self.getStatus():
            self.toggleConfiguration()
        try:
            command = [self.Protocol, "show", self.Name, "endpoints"]
            data_usage = subprocess.check_output(command, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError:
            return "stopped"

        data_usage = data_usage.decode("UTF-8").split()
        count = 0
        with self.engine.begin() as conn:
            for _ in range(int(len(data_usage) / 2)):
                conn.execute(
                    self.peersTable.update().values({
                        "endpoint": data_usage[count + 1]
                    }).where(
                        self.peersTable.c.id == data_usage[count]
                    )
                )
                count += 2

    def toggleConfiguration(self) -> tuple[bool, str] | tuple[bool, None]:
        self.getStatus()
        if self.Status:
            try:
                command = [f"{self.Protocol}-quick", "down", self.Name]
                check = subprocess.check_output(command, stderr=subprocess.STDOUT)

                self.removeAutostart()
            except subprocess.CalledProcessError as exc:
                return False, str(exc.output.strip().decode("utf-8"))
        else:
            try:
                command = [f"{self.Protocol}-quick", "up", self.Name]
                check = subprocess.check_output(command, stderr=subprocess.STDOUT)

                self.addAutostart()
            except subprocess.CalledProcessError as exc:
                return False, str(exc.output.strip().decode("utf-8"))
        self.__parseConfigurationFile()
        self.getStatus()
        return True, None

    def getPeersList(self):
        return self.Peers

    def getRestrictedPeersList(self) -> list:
        self.getRestrictedPeers()
        return self.RestrictedPeers

    def toJson(self):
        self.Status = self.getStatus()
        return {
            "Status": self.Status,
            "Name": self.Name,
            "PrivateKey": self.PrivateKey,
            "PublicKey": self.PublicKey,
            "Address": self.Address,
            "ListenPort": self.ListenPort,
            "PreUp": self.PreUp,
            "PreDown": self.PreDown,
            "PostUp": self.PostUp,
            "PostDown": self.PostDown,
            "SaveConfig": self.SaveConfig,
            "DataUsage": {
                "Total": sum(p.metered_usage()["total"] for p in self.Peers),
                "Sent": sum(p.metered_usage()["sent"] for p in self.Peers),
                "Receive": sum(p.metered_usage()["receive"] for p in self.Peers)
            },
            "ConnectedPeers": len(list(filter(lambda x: x.status == "running", self.Peers))),
            "TotalPeers": len(self.Peers),
            "Protocol": self.Protocol,
            "Table": self.Table,
            "Info": self.configurationInfo.model_dump()
        }

    def backupConfigurationFile(self) -> tuple[bool, dict[str, str]]:
        if not os.path.exists(os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup')):
            os.mkdir(os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup'))
        time = datetime.now().strftime("%Y%m%d%H%M%S")
        shutil.copy(
            self.configPath,
            os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup', f'{self.Name}_{time}.conf')
        )
        with open(os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup', f'{self.Name}_{time}.sql'), 'w+') as f:
            for l in self.__dumpDatabase():
                f.write(l + "\n")

        return True, {
            "filename": f'{self.Name}_{time}.conf',
            "backupDate": datetime.now().strftime("%Y%m%d%H%M%S")
        }

    def getBackups(self, databaseContent: bool = False) -> list[dict[str, str]]:
        backups = []

        directory = os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup')
        files = [(file, os.path.getctime(os.path.join(directory, file)))
                 for file in os.listdir(directory) if os.path.isfile(os.path.join(directory, file))]
        files.sort(key=lambda x: x[1], reverse=True)

        for f, ct in files:
            if RegexMatch(rf"^({self.Name})_(\d+)\\.(conf)$", f):
                s = re.search(rf"^({self.Name})_(\d+)\\.(conf)$", f)
                date = s.group(2)
                d = {
                    "filename": f,
                    "backupDate": date,
                    "content": open(os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup', f), 'r').read()
                }
                if f.replace(".conf", ".sql") in list(os.listdir(directory)):
                    d['database'] = True
                    if databaseContent:
                        d['databaseContent'] = open(os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup', f.replace(".conf", ".sql")), 'r').read()
                backups.append(d)

        return backups

    def restoreBackup(self, backupFileName: str) -> bool:
        backups = list(map(lambda x : x['filename'], self.getBackups()))
        if backupFileName not in backups:
            return False
        if self.Status:
            self.toggleConfiguration()
        target = os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup', backupFileName)
        targetSQL = os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup', backupFileName.replace(".conf", ".sql"))
        if not os.path.exists(target):
            return False
        targetContent = open(target, 'r').read()
        try:
            with open(self.configPath, 'w') as f:
                f.write(targetContent)
        except Exception as e:
            return False
        self.__parseConfigurationFile()
        self.__importDatabase(targetSQL)
        self.__initPeersList()
        return True

    def deleteBackup(self, backupFileName: str) -> bool:
        backups = list(map(lambda x : x['filename'], self.getBackups()))
        if backupFileName not in backups:
            return False
        try:
            os.remove(os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup', backupFileName))
        except Exception as e:
            return False
        return True

    def downloadBackup(self, backupFileName: str) -> tuple[bool, str] | tuple[bool, None]:
        backup = list(filter(lambda x : x['filename'] == backupFileName, self.getBackups()))
        if len(backup) == 0:
            return False, None
        zip = f'{str(uuid.UUID(int=random.Random().getrandbits(128), version=4))}.zip'
        with ZipFile(os.path.join('download', zip), 'w') as zipF:
            zipF.write(
                os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup', backup[0]['filename']),
                os.path.basename(os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup', backup[0]['filename']))
            )
            if backup[0]['database']:
                zipF.write(
                    os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup', backup[0]['filename'].replace('.conf', '.sql')),
                    os.path.basename(os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup', backup[0]['filename'].replace('.conf', '.sql')))
                )

        return True, zip

    def updateConfigurationSettings(self, newData: dict) -> tuple[bool, str]:
        if self.Status:
            self.toggleConfiguration()
        original = []
        dataChanged = False
        with open(self.configPath, 'r') as f:
            original = [l.rstrip("\n") for l in f.readlines()]
            allowEdit = ["Address", "PreUp", "PostUp", "PreDown", "PostDown", "ListenPort", "Table"]
            if self.Protocol == 'awg':
                allowEdit += ["Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4", "I1", "I2", "I3", "I4", "I5"]
            start = original.index("[Interface]")
            try:
                end = original.index("[Peer]")
            except ValueError as e:
                end = len(original)
            new = ["[Interface]"]
            peerFound = False
            for line in range(start, end):
                split = re.split(r'\s*=\s*', original[line], 1)
                if len(split) == 2:
                    if split[0] not in allowEdit:
                        new.append(original[line])
            for key in allowEdit:
                new.insert(1, f"{key} = {str(newData[key]).strip()}")
            new.append("")
            for line in range(end, len(original)):
                new.append(original[line])
            self.backupConfigurationFile()
            with open(self.configPath, 'w') as f:
                f.write("\n".join(new))

        status, msg = self.toggleConfiguration()
        if not status:
            return False, msg
        for i in allowEdit:
            setattr(self, i, str(newData[i]))
                
        return True, ""

    def deleteConfiguration(self):
        if self.getStatus():
            self.toggleConfiguration()
        os.remove(self.configPath)
        self.__dropDatabase()
        return True

    def renameConfiguration(self, newConfigurationName) -> tuple[bool, str]:
        newConfigurationName = os.path.basename(newConfigurationName)

        if len(newConfigurationName) > 15 or not re.match(r'^[a-zA-Z0-9_=\+\.\-]{1,15}$', newConfigurationName):
            return False, "Configuration name is either too long or contains an illegal character"
        
        newConfigurationName = newConfigurationName.replace("`", "") # double check
    
        try:
            if self.getStatus():
                self.toggleConfiguration()
            self.createDatabase(newConfigurationName)
            with self.engine.begin() as conn:
                def doRenameStatement(suffix):
                    newConfig = f"{newConfigurationName}{suffix}"
                    oldConfig = f"{self.Name}{suffix}"

                    conn.execute(
                        sqlalchemy.text(
                            f'INSERT INTO `{newConfig}` SELECT * FROM `{oldConfig}`'
                        )
                    )

                doRenameStatement("")
                doRenameStatement("_restrict_access")
                doRenameStatement("_deleted")
                doRenameStatement("_transfer")

            self.AllPeerJobs.updateJobConfigurationName(self.Name, newConfigurationName)
            shutil.copy(
                self.configPath,
                os.path.join(self.__getProtocolPath(), f'{newConfigurationName}.conf')
            )
            self.deleteConfiguration()
        except Exception as e:
            current_app.logger.error(f"Failed to rename configuration.\nNew Configuration Name: {newConfigurationName}\nError: {str(e)}")
            return False, "Internal server error"
        return True, None

    @locked_peer_state
    def getNumberOfAvailableIP(self):
        if len(self.Address) < 0:
            return False, None
        existedAddress = set()
        availableAddress = {}
        with self.engine.connect() as conn:
            leases = self._lease_rows(conn)
        for p in leases:
            peerAllowedIP = (p["allowed_ip"] or "").split(',')
            for pip in peerAllowedIP:
                # Legacy restricted peers can have N/A instead of an IP.
                # Skip the placeholder without noisy exception logging.
                if pip.strip().upper() == "N/A":
                    continue
                ppip = pip.strip().split('/')
                if len(ppip) == 2:
                    try:
                        check = ipaddress.ip_network(ppip[0])
                        existedAddress.add(check)
                    except Exception as e:
                        current_app.logger.warning("%s has an invalid stored peer address", self.Name)
        configurationAddresses = self.Address.split(',')
        for ca in configurationAddresses:
            ca = ca.strip()
            caSplit = ca.split('/')
            try:
                if len(caSplit) == 2:
                    network = ipaddress.ip_network(ca, False)
                    existedAddress.add(ipaddress.ip_network(caSplit[0]))
                    availableAddress[ca] = network.num_addresses
                    for p in existedAddress:
                        if p.version == network.version and p.subnet_of(network):
                            availableAddress[ca] -= 1
            except Exception as e:
                current_app.logger.warning("Failed to parse IP address %s from %s: %s", ca, self.Name, e)
        return True, availableAddress

    @locked_peer_state
    def getAvailableIP(self, threshold = 255):
        if len(self.Address) < 0:
            return False, None
        existedAddress = set()
        availableAddress = {}
        with self.engine.connect() as conn:
            leases = self._lease_rows(conn)
        for p in leases:
            peerAllowedIP = (p["allowed_ip"] or "").split(',')
            for pip in peerAllowedIP:
                # Legacy restricted peers can have N/A instead of an IP.
                # Skip the placeholder without noisy exception logging.
                if pip.strip().upper() == "N/A":
                    continue
                ppip = pip.strip().split('/')
                if len(ppip) == 2:
                    try:
                        check = ipaddress.ip_network(ppip[0])
                        existedAddress.add(check.compressed)
                    except Exception as e:
                        current_app.logger.warning("%s has an invalid stored peer address", self.Name)
        configurationAddresses = self.Address.split(',')
        for ca in configurationAddresses:
            ca = ca.strip()
            caSplit = ca.split('/')
            try:
                if len(caSplit) == 2:
                    network = ipaddress.ip_network(ca, False)
                    existedAddress.add(ipaddress.ip_network(caSplit[0]).compressed)
                    if threshold == -1:
                        availableAddress[ca] = filter(lambda ip : ip not in existedAddress,
                                                      map(lambda iph : ipaddress.ip_network(iph).compressed, network.hosts()))
                    else:
                        availableAddress[ca] = list(islice(filter(lambda ip : ip not in existedAddress,
                                                                  map(lambda iph : ipaddress.ip_network(iph).compressed, network.hosts())), threshold))
            except Exception as e:
                current_app.logger.warning("Failed to parse IP address %s from %s: %s", ca, self.Name, e)
        return True, availableAddress

    def getRealtimeTrafficUsage(self):
        """Non-blocking MB/s using successive API poll snapshots.

        The previous implementation slept for a full second on each request,
        tying up a synchronous Gunicorn worker. The initial sample just seeds
        the counter baseline; subsequent samples calculate an elapsed-time rate.
        """
        stat = psutil.net_io_counters(pernic=True, nowrap=True).get(self.Name)
        if stat is None:
            self._realtime_traffic_sample = None
            return {"sent": 0, "recv": 0}
        sample = (time.monotonic(), stat.bytes_recv, stat.bytes_sent)
        previous = getattr(self, "_realtime_traffic_sample", None)
        self._realtime_traffic_sample = sample
        if previous is None:
            return {"sent": 0, "recv": 0}
        elapsed = sample[0] - previous[0]
        if elapsed <= 0 or sample[1] < previous[1] or sample[2] < previous[2]:
            return {"sent": 0, "recv": 0}
        return {
            "recv": round((sample[1] - previous[1]) / elapsed / (1024 ** 2), 3),
            "sent": round((sample[2] - previous[2]) / elapsed / (1024 ** 2), 3),
        }
    
    '''
    Manager WireGuard Configuration Information
    '''
    
    def readConfigurationInfo(self):
        with self.engine.connect() as conn:
            result = conn.execute(
                self.infoTable.select().where(
                    self.infoTable.c.ID == self.Name
                )
            ).mappings().fetchone()
        return result
    
    def initConfigurationInfo(self):
        with self.engine.begin() as conn:
            conn.execute(
                self.infoTable.insert().values(
                    {
                        "ID": self.Name,
                        "Info": self.configurationInfo.model_dump_json()
                    }
                )
            )
    
    def storeConfigurationInfo(self):
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    self.infoTable.update().values(
                        {
                            "Info": self.configurationInfo.model_dump_json()
                        }
                    ).where(
                        self.infoTable.c.ID == self.Name
                    )
                )
        except Exception as e:
            return False
        
    def updateConfigurationInfo(self, key: str, value: str | dict[str, str] | dict[str, dict] | bool) -> tuple[bool, Any, str] | tuple[
        bool, str, None] | tuple[bool, None, None]:
        if key == "Description":
            self.configurationInfo.Description = value
        elif key == "OverridePeerSettings":
            for (key, val) in value.items():
                try:
                    status, msg = self.__validateOverridePeerSettings(key, jinja2.Template(val).render(configuration=self.toJson()))
                    if not status:
                        return False, msg, key
                except Exception as e:
                    return False, str(e), None
            self.configurationInfo.OverridePeerSettings = (
                self.configurationInfo.OverridePeerSettings.model_validate(value))
        elif key == "PeerGroups":
            peerGroups = {}
            for name, data in value.items():
                peerGroups[name] = PeerGroupsClass(**data)
            self.configurationInfo.PeerGroups = peerGroups
        elif key == "PeerTrafficTracking":
            self.configurationInfo.PeerTrafficTracking = value
        elif key == "PeerHistoricalEndpointTracking":
            self.configurationInfo.PeerHistoricalEndpointTracking = value
        else: 
            return False, "Key does not exist", None
        self.storeConfigurationInfo()
        return True, None, None
    
    def __validateOverridePeerSettings(self, key: str, value: str | int) -> tuple[bool, None] | tuple[bool, str]:
        status = True
        msg = None
        if key == "DNS" and value:
            status, msg = ValidateDNSAddress(value)
        elif key == "EndpointAllowedIPs" and value:
            status, msg = ValidateEndpointAllowedIPs(value)
        elif key == "ListenPort" and value:
            if not value.isnumeric() or not (1 <= int(value) <= 65535):
                status = False
                msg = "Listen Port must be >= 1 and <= 65535"        
        return status, msg
        
    def getTransferTableSize(self):
        with self.engine.connect() as db:
            row_count = db.execute(
                sqlalchemy.select(sqlalchemy.func.count()).select_from(self.peersTransferTable)
            ).scalar()
            return int(row_count)

    def getHistoricalEndpointTableSize(self):
        with self.engine.connect() as db:
            row_count = db.execute(
                sqlalchemy.select(sqlalchemy.func.count()).select_from(self.peersHistoryEndpointTable)
            ).scalar()
            return int(row_count)
        
    def downloadTransferTable(self):
        with self.engine.connect() as db:
            data = db.execute(
                self.peersTransferTable.select()
            ).mappings().fetchall()
            return data

    def downloadHistoricalEndpointTable(self):
        with self.engine.connect() as db:
            data = db.execute(
                self.peersHistoryEndpointTable.select()
            ).mappings().fetchall()
            return data
    
    def deleteTransferTable(self):
        try:
            with self.engine.begin() as db:
                db.execute(
                    self.peersTransferTable.delete()
            )
            with self.engine.connect() as conn:
                if conn.dialect.name == 'sqlite':
                    print("[WGDashboard] SQLite Vacuuming Database")
                    conn.execute(sqlalchemy.text('VACUUM;'))
        except Exception as e:
            return False
        return True

    def deleteHistoryEndpointTable(self):
        try:
            with self.engine.begin() as db:
                db.execute(
                    self.peersHistoryEndpointTable.delete()
                )
            with self.engine.connect() as conn:
                if conn.dialect.name == 'sqlite':
                    print("[WGDashboard] SQLite Vacuuming Database")
                    conn.execute(sqlalchemy.text('VACUUM;'))
        except Exception as e:
            return False
        return True
