"""Pytest configuration and fixtures for integration tests."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone


@pytest.fixture
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_event_bus():
    """Mock event bus for testing."""
    bus = MagicMock()
    bus.publish = AsyncMock()
    bus.subscribe = MagicMock()
    bus.unsubscribe = MagicMock()
    return bus


@pytest.fixture
def mock_supabase():
    """Mock Supabase client."""
    client = MagicMock()
    client.table = MagicMock(return_value=MagicMock())
    client.table().insert = MagicMock(return_value=MagicMock(data=[]))
    client.table().update = MagicMock(return_value=MagicMock(data=[]))
    client.table().select = MagicMock(return_value=MagicMock(data=[]))
    return client


@pytest.fixture
def container():
    """Mock DI container."""
    container = MagicMock()
    container.try_resolve = MagicMock(return_value=None)
    return container


@pytest.fixture
async def director_instance(mock_event_bus, mock_supabase, container):
    """Create Director instance for testing."""
    from services.director import Director
    
    director = Director(event_bus=mock_event_bus, supabase_client=mock_supabase, container=container)
    director._shared_bus = mock_event_bus
    return director


@pytest.fixture
async def condition_engine_instance(mock_event_bus, mock_supabase, container):
    """Create ConditionEngine instance for testing."""
    from services.condition_engine import ConditionEngine
    
    engine = ConditionEngine(event_bus=mock_event_bus, supabase_client=mock_supabase, container=container)
    return engine


@pytest.fixture
async def meta_engine_instance(mock_event_bus, mock_supabase, container):
    """Create MetaEngine instance for testing."""
    from services.meta_engine import MetaEngineV7
    
    engine = MetaEngineV7(event_bus=mock_event_bus, container=container, supabase_client=mock_supabase)
    return engine


@pytest.fixture
def sample_observation():
    """Sample observation for testing."""
    return {
        "brand_id": "test_brand_1",
        "topic": "Python testing best practices",
        "engagement": 45.5,
        "posting_hour": 14,
        "has_cta": True,
        "metadata": {"industry": "tech"},
    }


@pytest.fixture
def sample_decision():
    """Sample decision for testing."""
    return {
        "brand_id": "test_brand_1",
        "recommended_action": "increase_similar_content",
        "confidence": 0.88,
        "reason": "High engagement detected",
        "evidence": {"baseline": 30, "engagement_ratio": 1.5},
    }


@pytest.fixture
def sample_engine_alert():
    """Sample engine alert for testing."""
    return {
        "engine": "content_engine",
        "status": "degraded",
        "message": "High error rate detected",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
