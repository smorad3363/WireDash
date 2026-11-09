"""
AmneziaWG Configuration
"""
import random, sqlalchemy, os, subprocess, re, uuid
from flask import current_app
from .PeerJobs import PeerJobs
from .AmneziaPeer import AmneziaPeer
from .PeerShareLinks import PeerShareLinks
from .Utilities import RegexMatch, CheckAddress, CheckPeerKey
from .PeerState import locked_peer_state
from .WireguardConfiguration import WireguardConfiguration
from .DashboardWebHooks import DashboardWebHooks


class AmneziaConfiguration(WireguardConfiguration):
    def __init__(self,
                 DashboardConfig,
                 AllPeerJobs: PeerJobs,
                 AllPeerShareLinks: PeerShareLinks,
                 DashboardWebHooks: DashboardWebHooks,
                 name: str = None,
                 data: dict = None,
                 backup: dict = None,
                 startup: bool = False):
        self.Jc = 0
        self.Jmin = 0
        self.Jmax = 0
        self.S1 = 0
        self.S2 = 0
        self.S3 = 0
        self.S4 = 0
        self.H1 = 1
        self.H2 = 2
        self.H3 = 3
        self.H4 = 4
        self.I1 = "0"
        self.I2 = "0"
        self.I3 = "0"
        self.I4 = "0"
        self.I5 = "0"

        super().__init__(DashboardConfig, AllPeerJobs, AllPeerShareLinks, DashboardWebHooks, name, data, backup, startup, wg=False)

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
            "Info": self.configurationInfo.model_dump(),
            "DataUsage": {
                "Total": sum(p.metered_usage()["total"] for p in self.Peers),
                "Sent": sum(p.metered_usage()["sent"] for p in self.Peers),
                "Receive": sum(p.metered_usage()["receive"] for p in self.Peers)
            },
            "ConnectedPeers": len(list(filter(lambda x: x.status == "running", self.Peers))),
            "TotalPeers": len(self.Peers),
            "Protocol": self.Protocol,
            "Table": self.Table,
            "Jc": self.Jc,
            "Jmin": self.Jmin,
            "Jmax": self.Jmax,
            "S1": self.S1,
            "S2": self.S2,
            "S3": self.S3,
            "S4": self.S4,
            "H1": self.H1,
            "H2": self.H2,
            "H3": self.H3,
            "H4": self.H4,
            "I1": self.I1,
            "I2": self.I2,
            "I3": self.I3,
            "I4": self.I4,
            "I5": self.I5
        }

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

    def _peer_type(self):
        return AmneziaPeer

    def getPeers(self):
        return super().getPeers()

    def addPeers(self, peers: list) -> tuple[bool, list, str]:
        return super().addPeers(peers)

    @locked_peer_state
    def getRestrictedPeers(self):
        with self.engine.connect() as conn:
            restricted = conn.execute(self.peersRestrictedTable.select()).mappings().fetchall()
        cached = {p.id: p for p in getattr(self, "RestrictedPeers", [])}
        peers = []
        for row in restricted:
            peer = cached.get(row["id"])
            if peer is None:
                peer = AmneziaPeer(row, self)
            else:
                peer.refreshFromRow(row)
            peers.append(peer)
        self.RestrictedPeers = peers
