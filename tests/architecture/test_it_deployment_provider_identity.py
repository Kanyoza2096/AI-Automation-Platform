from plugins.industries.it_operations.repository import ITOperationsRepository


def test_it_provider_identity_and_version_mapping_are_explicit():
    repo = ITOperationsRepository()
    service = repo.add_service("tenant-a", {"name": "api", "environment": "production"})
    binding = repo.bind_service_provider("tenant-a", service.id, {
        "provider": "render", "provider_service_id": "srv-render-1", "environment": "production"
    })
    ref = repo.bind_deployment_version("tenant-a", service.id, {
        "provider": "render", "version": "2026.10.02.1", "provider_version_ref": "abc123"
    })
    assert repo.get_service_provider_binding("tenant-a", service.id).provider_service_id == "srv-render-1"
    assert repo.get_deployment_version_reference("tenant-a", service.id, "2026.10.02.1").provider_version_ref == "abc123"
    assert binding.environment == "production"
    assert ref.provider == "render"


def test_identity_mapping_is_workspace_scoped():
    repo = ITOperationsRepository()
    service = repo.add_service("tenant-a", {"name": "api"})
    repo.bind_service_provider("tenant-a", service.id, {"provider": "render", "provider_service_id": "srv-1"})
    assert repo.get_service_provider_binding("tenant-b", service.id) is None
