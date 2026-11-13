import asyncio

from services.engagement_tracker import EngagementTracker


def test_engagement_tracker_is_not_healthy_without_supabase_provider():
    tracker = EngagementTracker(supabase_client=None)
    tracker._running = True

    report = asyncio.run(tracker.check_health_async())

    assert report["status"] == "unknown"


def test_engagement_tracker_reports_stopped_when_not_running():
    tracker = EngagementTracker(supabase_client=object())

    report = asyncio.run(tracker.check_health_async())

    assert report["status"] == "stopped"
