"""
Peer Jobs
"""
import sqlalchemy
import uuid
from datetime import timedelta
from .PeerLimits import quota_payload, quota_reached, parse_quota_payload

from .DatabaseConnection import ConnectionString
from .PeerJob import PeerJob
from .PeerJobLogger import PeerJobLogger
import sqlalchemy as db
from datetime import datetime
from flask import current_app

class PeerJobs:
    def __init__(self, DashboardConfig, WireguardConfigurations, AllPeerShareLinks):
        self.Jobs: list[PeerJob] = []
        self.engine = db.create_engine(ConnectionString('wgdashboard_job'))
        self.metadata = db.MetaData()
        self.peerJobTable = db.Table('PeerJobs', self.metadata,
                                     db.Column('JobID', db.String(255), nullable=False, primary_key=True),
                                     db.Column('Configuration', db.String(255), nullable=False),
                                     db.Column('Peer', db.String(255), nullable=False),
                                     db.Column('Field', db.String(255), nullable=False),
                                     db.Column('Operator', db.String(255), nullable=False),
                                     db.Column('Value', db.String(255), nullable=False),
                                     db.Column('CreationDate', (db.DATETIME if DashboardConfig.GetConfig("Database", "type")[1] == 'sqlite' else db.TIMESTAMP), nullable=False),
                                     db.Column('ExpireDate', (db.DATETIME if DashboardConfig.GetConfig("Database", "type")[1] == 'sqlite' else db.TIMESTAMP)),
                                     db.Column('Action', db.String(255), nullable=False),
                                     )
        self.metadata.create_all(self.engine)
        self.__getJobs()
        self.JobLogger: PeerJobLogger = PeerJobLogger(self, DashboardConfig)
        self.WireguardConfigurations = WireguardConfigurations
        self.AllPeerShareLinks = AllPeerShareLinks
        self.cleanJob(init=True)

    def __getJobs(self):
        self.Jobs.clear()
        with self.engine.connect() as conn:
            jobs = conn.execute(self.peerJobTable.select().where(
                self.peerJobTable.columns.ExpireDate.is_(None)
            )).mappings().fetchall()
            for job in jobs:
                self.Jobs.append(PeerJob(
                    job['JobID'], job['Configuration'], job['Peer'], job['Field'], job['Operator'], job['Value'],
                    job['CreationDate'], job['ExpireDate'], job['Action']))

    def getAllJobs(self, configuration: str = None):
        if configuration is not None:
            with self.engine.connect() as conn:
                jobs = conn.execute(self.peerJobTable.select().where(
                    self.peerJobTable.columns.Configuration == configuration
                )).mappings().fetchall()
                j = []
                for job in jobs:
                    j.append(PeerJob(
                        job['JobID'], job['Configuration'], job['Peer'], job['Field'], job['Operator'], job['Value'],
                        job['CreationDate'], job['ExpireDate'], job['Action']))
                return j
        return []

    def toJson(self):
        return [x.toJson() for x in self.Jobs]

    def searchJob(self, Configuration: str, Peer: str):
        return list(filter(lambda x: x.Configuration == Configuration and x.Peer == Peer, self.Jobs))

    def searchJobById(self, JobID):
        return list(filter(lambda x: x.JobID == JobID, self.Jobs))

    def saveJob(self, Job: PeerJob) -> tuple[bool, list] | tuple[bool, str]:
        import traceback
        try:
            if Job.Field == "quota_total_data":
                parse_quota_payload(Job.Value)
                if Job.Action != "restrict":
                    return False, "Quota rules may only restrict access"
            with self.engine.begin() as conn:
                currentJob = self.searchJobById(Job.JobID)
                if len(currentJob) == 0:
                    conn.execute(
                        self.peerJobTable.insert().values(
                            {
                                "JobID": Job.JobID,
                                "Configuration": Job.Configuration,
                                "Peer": Job.Peer,
                                "Field": Job.Field,
                                "Operator": Job.Operator,
                                "Value": Job.Value,
                                "CreationDate": datetime.now(),
                                "ExpireDate": None,
                                "Action": Job.Action
                            }
                        )
                    )
                    if Job.Field == "quota_total_data":
                        self.JobLogger.log(Job.JobID, Message="Traffic quota rule created")
                    else:
                        self.JobLogger.log(Job.JobID, Message=f"Job is created if {Job.Field} {Job.Operator} {Job.Value} then {Job.Action}")
                else:
                    conn.execute(
                        self.peerJobTable.update().values({
                            "Field": Job.Field,
                            "Operator": Job.Operator,
                            "Value": Job.Value,
                            "Action": Job.Action
                        }).where(self.peerJobTable.columns.JobID == Job.JobID)
                    )
                    if Job.Field == "quota_total_data" or currentJob[0].Field == "quota_total_data":
                        self.JobLogger.log(Job.JobID, Message="Traffic quota rule updated")
                    else:
                        self.JobLogger.log(Job.JobID, Message=f"Job is updated from if {currentJob[0].Field} {currentJob[0].Operator} {currentJob[0].Value} then {currentJob[0].Action}; to if {Job.Field} {Job.Operator} {Job.Value} then {Job.Action}")
            self.__getJobs()
            self.WireguardConfigurations.get(Job.Configuration).searchPeer(Job.Peer)[1].getJobs()
            return True, list(
                filter(lambda x: x.Configuration == Job.Configuration and x.Peer == Job.Peer and x.JobID == Job.JobID,
                       self.Jobs))
        except Exception as e:
            traceback.print_exc()
            return False, str(e)

    def deleteJob(self, Job: PeerJob) -> tuple[bool, None] | tuple[bool, str]:
        try:
            if len(self.searchJobById(Job.JobID)) == 0:
                return False, "Job does not exist"
            with self.engine.begin() as conn:
                conn.execute(
                    self.peerJobTable.update().values(
                        {
                            "ExpireDate": datetime.now()
                        }
                    ).where(self.peerJobTable.columns.JobID == Job.JobID)
                )
                self.JobLogger.log(Job.JobID, Message=f"Job is removed due to being deleted or finished.")
            self.__getJobs()
            self.WireguardConfigurations.get(Job.Configuration).searchPeer(Job.Peer)[1].getJobs()
            return True, None
        except Exception as e:
            return False, str(e)

    def updateJobConfigurationName(self, ConfigurationName: str, NewConfigurationName: str) -> tuple[bool, str] | tuple[bool, None]:
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    self.peerJobTable.update().values({
                        "Configuration": NewConfigurationName
                    }).where(self.peerJobTable.columns.Configuration == ConfigurationName)
                )
            self.__getJobs()
            return True, None
        except Exception as e:
            return False, str(e)
    
    def provision_creation_limits(self, configuration, peer_ids, days, quota_gb, traffic_factor):
        """Create all policy jobs atomically; never record metering weights in logs."""
        if not days and not quota_gb:
            return True, None
        if not peer_ids:
            return False, "No created peers"
        now = datetime.now()
        records = []
        for peer in peer_ids:
            for field, value in (
                ("date", (now + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S") if days else None),
                ("quota_total_data", quota_payload(quota_gb, traffic_factor) if quota_gb else None),
            ):
                if value is not None:
                    records.append({
                        "JobID": str(uuid.uuid4()), "Configuration": configuration,
                        "Peer": peer, "Field": field, "Operator": "lgt",
                        "Value": value, "CreationDate": now,
                        "ExpireDate": None, "Action": "restrict",
                    })
        try:
            with self.engine.begin() as conn:
                conn.execute(self.peerJobTable.insert(), records)
            self.__getJobs()
            # Peer models store their scheduled jobs locally for rendering.
            c = self.WireguardConfigurations.get(configuration)
            if c is not None:
                for peer in peer_ids:
                    found, target = c.searchPeer(peer)
                    if found:
                        target.getJobs()
            return True, None
        except Exception:
            return False, "Could not store peer limits"

    def getPeerJobLogs(self, configurationName):
        return self.JobLogger.getLogs(configurationName)


    def runJob(self):
        self.cleanJob()
        needToDelete = []
        self.__getJobs()
        for job in self.Jobs:
            c = self.WireguardConfigurations.get(job.Configuration)
            if c is not None:
                f, fp = c.searchPeer(job.Peer)
                if f:
                    try:
                        if job.Field in ["total_receive", "total_sent", "total_data", "quota_total_data"]:
                            # Cached Peer objects lag behind DB transfer refreshes. Read the
                            # most recently persisted raw upload/download counters instead.
                            with c.engine.connect() as conn:
                                row = conn.execute(
                                    c.peersTable.select().where(c.peersTable.c.id == fp.id)
                                ).mappings().fetchone()
                            if row is None:
                                continue
                            received = float(row["total_receive"] or 0) + float(row["cumu_receive"] or 0)
                            sent = float(row["total_sent"] or 0) + float(row["cumu_sent"] or 0)
                            if job.Field == "quota_total_data":
                                runAction = quota_reached(received, sent, job.Value)
                            else:
                                raw = {"total_receive": received, "total_sent": sent,
                                       "total_data": received + sent}[job.Field]
                                runAction = self.__runJob_Compare(raw, float(job.Value), job.Operator)
                        elif job.Field == "date":
                            x: datetime = datetime.now()
                            y: datetime = datetime.strptime(job.Value, "%Y-%m-%d %H:%M:%S")
                            runAction = self.__runJob_Compare(x, y, job.Operator)
                        else:
                            continue
                    except (ValueError, TypeError, KeyError, db.exc.SQLAlchemyError):
                        # Avoid spinning the scheduler on temporary database locks.
                        # Do not copy invalid policy payloads into user-facing logs.
                        self.JobLogger.log(job.JobID, False, "Invalid policy; review scheduled job")
                        continue
                    if runAction:
                        s = False
                        if job.Action == "restrict":
                            s, msg = c.restrictPeers([fp.id])
                        elif job.Action == "delete":
                            s, msg = c.deletePeers([fp.id], self, self.AllPeerShareLinks)
                        elif job.Action == "reset_total_data_usage":
                            s = fp.resetDataUsage("total")
                            c.restrictPeers([fp.id])
                            c.allowAccessPeers([fp.id])
                        if s is True:
                            self.JobLogger.log(job.JobID, s,
                                          f"Peer {fp.id} from {c.Name} is successfully {job.Action}ed."
                                          )
                            needToDelete.append(job)
                        else:
                            self.JobLogger.log(job.JobID, s,
                                          f"Peer {fp.id} from {c.Name} failed {job.Action}ed."
                                          )
                else:
                    self.JobLogger.log(job.JobID, False,
                                  f"Somehow can't find this peer {job.Peer} from {c.Name} failed {job.Action}ed."
                                  )
            else:
                self.JobLogger.log(job.JobID, False,
                              f"Somehow can't find this peer {job.Peer} from {job.Configuration} failed {job.Action}ed."
                              )
        for j in needToDelete:
            self.deleteJob(j)
            
    def cleanJob(self, init = False):
        failingJobs = self.JobLogger.getFailingJobs()
        with self.engine.begin() as conn:
            for job in failingJobs:
                conn.execute(
                    self.peerJobTable.update().values(
                        {
                            "ExpireDate": datetime.now()
                        }
                    ).where(self.peerJobTable.columns.JobID == job.get('JobID'))
                )
                self.JobLogger.deleteLogs(JobID=job.get('JobID'))
                self.JobLogger.log(job.get('JobID'), Message=f"Job is removed due to being stale.")
        
        with self.engine.connect() as conn:
            if init and conn.dialect.name == 'sqlite':
                print("[WGDashboard] SQLite Vacuuming PeerJobs Database")
                self.JobLogger.vacuum()
                conn.execute(sqlalchemy.text('VACUUM;'))

    def __runJob_Compare(self, x: float | datetime, y: float | datetime, operator: str):
        if operator == "eq":
            return x == y
        if operator == "neq":
            return x != y
        if operator == "lgt":
            return x > y
        if operator == "lst":
            return x < y