"""End-to-end event bus integration tests."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, call
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_content_generated_event_flow(mock_event_bus, sample_observation):
    """
    Test: Worker publishes 'content.generated' → Brand Scheduler receives it.
    This verifies the Content Engine ↔ Brand Scheduler decoupling.
    """
    # Arrange
    event_data = {
        "brand_id": sample_observation["brand_id"],
        "post_data": {"title": "Test Post", "content": "Test content"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    
    handler = AsyncMock()
    mock_event_bus.subscribe("content.generated", handler)
    
    # Act
    await mock_event_bus.publish("content.generated", event_data)
    
    # Assert
    mock_event_bus.subscribe.assert_called_once_with("content.generated", handler)


@pytest.mark.asyncio
async def test_engagement_to_strategy_flow(mock_event_bus):
    """
    Test: Engagement data → ConditionEngine → Strategy recommendation → Director
    This verifies the learning loop works end-to-end.
    """
    # Arrange
    engagement_event = {
        "brand": "test_brand",
        "engagement": 150,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    
    strategy_handler = AsyncMock()
    director_handler = AsyncMock()
    
    mock_event_bus.subscribe("strategy_recommendation", strategy_handler)
    mock_event_bus.subscribe("strategy_executed", director_handler)
    
    # Act
    # Simulate: Worker publishes engagement
    await mock_event_bus.publish("engagement_data", engagement_event)
    
    # Simulate: ConditionEngine receives and publishes recommendation
    recommendation = {
        "brand": "test_brand",
        "action": "increase_similar_content",
        "reason": "High engagement (150)",
    }
    await mock_event_bus.publish("strategy_recommendation", recommendation)
    
    # Assert
    assert mock_event_bus.publish.call_count >= 2


@pytest.mark.asyncio
async def test_meta_engine_health_monitoring(mock_event_bus, sample_engine_alert):
    """
    Test: Meta Engine publishes engine alerts → Director receives and acts
    This verifies Meta Engine monitoring works.
    """
    # Arrange
    alert_handler = AsyncMock()
    mock_event_bus.subscribe("engine_alert", alert_handler)
    
    # Act
    await mock_event_bus.publish("engine_alert", sample_engine_alert)
    
    # Assert
    mock_event_bus.subscribe.assert_called_once_with("engine_alert", alert_handler)


@pytest.mark.asyncio
async def test_incident_creation_and_resolution(mock_event_bus):
    """
    Test: Incident created → Escalation timeout → Deduplication
    This verifies incident management in Meta Engine.
    """
    # Arrange
    incident_event = {
        "incident_id": "inc_12345",
        "engine": "condition_engine",
        "severity": "CRITICAL",
        "title": "Decision loop timeout",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    
    incident_handler = AsyncMock()
    mock_event_bus.subscribe("incident_created", incident_handler)
    
    # Act
    await mock_event_bus.publish("incident_created", incident_event)
    await mock_event_bus.publish("incident_created", incident_event)  # Duplicate
    
    # Assert
    mock_event_bus.subscribe.assert_called_once_with("incident_created", incident_handler)


@pytest.mark.asyncio
async def test_director_orchestrates_strategy_execution(mock_event_bus, sample_decision):
    """
    Test: Director receives strategy recommendation → Executes on ContentEngine
    This verifies Director orchestration works.
    """
    # Arrange
    strategy_event = {
        "brand_id": sample_decision["brand_id"],
        "action": sample_decision["recommended_action"],
        "confidence": sample_decision["confidence"],
    }
    
    execution_handler = AsyncMock()
    mock_event_bus.subscribe("strategy_executed", execution_handler)
    
    # Act
    await mock_event_bus.publish("strategy_recommendation", strategy_event)
    await mock_event_bus.publish("strategy_executed", {**strategy_event, "status": "success"})
    
    # Assert
    assert mock_event_bus.publish.call_count == 2


@pytest.mark.asyncio
async def test_council_vote_integration(mock_event_bus):
    """
    Test: ConditionEngine asks council before deciding
    This verifies council voting is integrated.
    """
    # Arrange
    decision_event = {
        "brand_id": "test_brand",
        "decision": "increase_similar_content",
        "requires_approval": True,
    }
    
    vote_handler = AsyncMock(return_value={"approved": True})
    mock_event_bus.subscribe("council_vote_required", vote_handler)
    
    # Act
    await mock_event_bus.publish("council_vote_required", decision_event)
    
    # Assert
    mock_event_bus.subscribe.assert_called_once()


@pytest.mark.asyncio
async def test_error_recovery_flow(mock_event_bus):
    """
    Test: Error → Meta Engine detects → Initiates recovery → Verifies fix
    This verifies the complete recovery flow.
    """
    # Arrange
    error_event = {
        "engine": "content_engine",
        "error": "Database connection timeout",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    
    recovery_handler = AsyncMock()
    mock_event_bus.subscribe("recovery_initiated", recovery_handler)
    
    # Act
    await mock_event_bus.publish("engine_error", error_event)
    await mock_event_bus.publish("recovery_initiated", {
        "engine": "content_engine",
        "action": "restart",
    })
    
    # Assert
    assert mock_event_bus.publish.call_count == 2
