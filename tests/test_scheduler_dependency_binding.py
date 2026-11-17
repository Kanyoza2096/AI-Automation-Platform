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

def test_manager_does_not_erase_existing_dependencies_when_resolution_is_empty(monkeypatch):
    existing_job_service = object()
    existing_redis_client = object()

    class Container:
        def try_resolve(self, service_type):
            return None

    import services.supabase
    monkeypatch.setattr(
        services.supabase,
        "get_service_client_from_settings",
        lambda: None,
    )

    scheduler = SimpleNamespace(
        _job_service=existing_job_service,
        _distributed_lock=SimpleNamespace(redis=existing_redis_client),
    )
    manager = BrandSchedulerManager.__new__(BrandSchedulerManager)
    manager._schedulers = {"brand-1": scheduler}

    manager.set_container(Container())

    assert scheduler._job_service is existing_job_service
    assert scheduler._distributed_lock.redis is existing_redis_client


def test_scheduler_selects_article_factory_for_website_and_social_factory_otherwise(monkeypatch):
    from scheduler.brand_scheduler import BrandScheduler

    class Lock:
        mode = "DISTRIBUTED"

        def acquire(self, **kwargs):
            return True

        def release(self):
            pass

    class Semaphore:
        def acquire(self, blocking=False):
            return True

        def release(self):
            pass

    for platform, expected_factory in (
        ("website", "ArticlePublishWorkflowFactory"),
        ("facebook", "PostPublishWorkflowFactory"),
    ):
        selected = []

        class Container:
            def resolve(self, name):
                selected.append(name)

                def factory(**kwargs):
                    return object()

                return factory

        scheduler = BrandScheduler.__new__(BrandScheduler)
        scheduler.brand = SimpleNamespace(
            id=f"brand-{platform}",
            name=f"Brand {platform}",
            workspace_id="workspace-1",
            platform=platform,
        )
        scheduler.ai_profile = None
        scheduler._container = Container()
        scheduler._job_service = object()
        scheduler._distributed_lock = Lock()
        scheduler._publish_semaphore = Semaphore()
        scheduler._pipeline_status = {"status": "idle"}
        scheduler._emit = lambda *args, **kwargs: None
        scheduler._emit_dashboard = lambda *args, **kwargs: None

        # Stop immediately after the selected factory is instantiated. This
        # exercises the real scheduler branch without running a publish workflow.
        def stop_before_execution(*args, **kwargs):
            raise RuntimeError("test stop before workflow execution")

        monkeypatch.setattr(scheduler, "_execute_workflow", stop_before_execution)

        assert scheduler._trigger_workflow(source="routing-test") is False
        assert selected == [expected_factory]
