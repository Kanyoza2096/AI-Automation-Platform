"""AWS Connector Fabric boundary using boto3 credentials/role resolution.

The adapter deliberately wraps a small set of governed operations rather than
exposing arbitrary AWS API calls. A boto3 client/session may be injected for
unit tests or controlled runtime composition.
"""
from __future__ import annotations
from typing import Any, Optional

class AWSAdapter:
    provider = "aws"
    def __init__(self, *, region: str = "us-east-1", session: Any = None,
                 clients: Optional[dict[str, Any]] = None, allow_mutations: bool = False,
                 allow_high_risk: bool = False):
        self.region=region; self.allow_mutations=allow_mutations; self.allow_high_risk=allow_high_risk
        self._clients=clients or {}
        if session is None and not clients:
            try:
                import boto3
                session=boto3.Session(region_name=region)
            except ImportError as exc:
                raise RuntimeError("boto3 is required for live AWS operations") from exc
        self.session=session
    def _client(self, service):
        if service not in self._clients: self._clients[service]=self.session.client(service,region_name=self.region)
        return self._clients[service]
    def _require(self, high=False, confirm=False):
        if not self.allow_mutations: raise PermissionError("aws mutations are disabled; enable them explicitly")
        if high and not self.allow_high_risk: raise PermissionError("aws high-risk operations are disabled")
        if high and not confirm: raise PermissionError("high-risk operation requires explicit confirm=True")
    @staticmethod
    def _receipt(status, object_id=None, error=None, metadata=None):
        return {"ok":status=="succeeded","provider":"aws","platform":"aws","status":status,"outcome":status,
                "provider_object_id":str(object_id) if status=="succeeded" and object_id is not None else None,
                "observed_object_id":str(object_id) if status!="succeeded" and object_id is not None else None,
                "error":error,"metadata":metadata or {}}
    def describe_service(self, *, cluster: str, service: str):
        return self._client("ecs").describe_services(cluster=cluster,services=[service])
    def update_ecs_service(self, *, cluster: str, service: str, desired_count: int, confirm=False):
        self._require(confirm=confirm)
        if desired_count < 0: raise ValueError("desired_count cannot be negative")
        try:
            out=self._client("ecs").update_service(cluster=cluster,service=service,desiredCount=desired_count)
            svc=(out.get("service") or {}); oid=svc.get("serviceArn")
            return self._receipt("succeeded" if oid else "uncertain",oid,metadata={"desired_count":desired_count,"reconciliation_required":not bool(oid)})
        except Exception as exc:
            return self._receipt("uncertain",error=f"AWS mutation outcome is ambiguous: {type(exc).__name__}",metadata={"reconciliation_required":True})
    def put_object(self, *, bucket: str, key: str, body: bytes, content_type: Optional[str] = None):
        self._require()
        if not bucket or not key: raise ValueError("bucket and key are required")
        kwargs={"Bucket":bucket,"Key":key,"Body":body}
        if content_type: kwargs["ContentType"]=content_type
        try:
            out=self._client("s3").put_object(**kwargs); oid=out.get("ETag") or f"s3://{bucket}/{key}"
            return self._receipt("succeeded",oid,metadata={"bucket":bucket,"key":key})
        except Exception as exc:
            return self._receipt("uncertain",error=f"AWS mutation outcome is ambiguous: {type(exc).__name__}",metadata={"reconciliation_required":True,"bucket":bucket,"key":key})
