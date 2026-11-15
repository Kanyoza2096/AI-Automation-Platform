from types import SimpleNamespace

from scheduler.brand_scheduler import BrandSchedulerManager


def test_manager_rebinds_existing_schedulers_to_container_services(monkeypatch):
    from services.job_service import JobService
    from core.redis_client import UpstashRedisClient

    job_service = object()
    redis_client = object()

    class Container:
        def try_resolve(self, service_type):
            if service_type is JobService:
                return job_service
            if service_type is UpstashRedisClient:
                return redis_client
            return None

    # Avoid creating a real Supabase client in this dependency-wiring unit test.
    import services.supabase
    monkeypatch.setattr(
        services.supabase,
        "get_service_client_from_settings",
        lambda: None,
    )

    scheduler = SimpleNamespace(
        _job_service=None,
        _distributed_lock=SimpleNamespace(redis=None),
    )
    manager = BrandSchedulerManager.__new__(BrandSchedulerManager)
    manager._schedulers = {"brand-1": scheduler}

    manager.set_container(Container())

    assert scheduler._job_service is job_service
    assert scheduler._distributed_lock.redis is redis_client
