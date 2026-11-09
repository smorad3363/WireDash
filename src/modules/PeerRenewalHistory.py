"""Compare actual effective time restrictions, not a single duplicate job."""
from .PeerCardSummary import utc_naive


def expiry_transition(history, job):
    def valid(item):
        return (item.Field=='date' and item.Action=='restrict' and item.Operator=='lgt')
    relevant=[item for item in history if valid(item)]
    active=[item for item in relevant if item.ExpireDate is None]
    before_values=[utc_naive(item.Value,local=True) for item in active]
    before_values=[value for value in before_values if value]
    if before_values:
        before=min(before_values)
    else:
        historic=sorted(relevant,key=lambda item:str(item.CreationDate),reverse=True)
        before=utc_naive(historic[0].Value,local=True) if historic else None
    after_values=[utc_naive(item.Value,local=True) for item in active if item.JobID!=job.JobID]
    after_values.append(utc_naive(job.Value,local=True) if valid(job) else None)
    after_values=[value for value in after_values if value]
    after=min(after_values) if after_values else None
    return before,after
