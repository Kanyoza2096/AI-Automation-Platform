"""Load testing with concurrent brand simulation."""

import asyncio
import time
import random
from dataclasses import dataclass
from typing import List
from datetime import datetime, timezone


@dataclass
class LoadTestConfig:
    """Configuration for load testing."""
    num_concurrent_brands: int = 100
    duration_seconds: int = 300
    events_per_brand_per_second: float = 0.5
    event_types: List[str] = None
    
    def __post_init__(self):
        if self.event_types is None:
            self.event_types = [
                "engagement_data",
                "post_published",
                "strategy_recommendation",
                "engine_alert",
            ]


@dataclass
class LoadTestMetrics:
    """Metrics collected during load test."""
    total_events: int = 0
    successful_events: int = 0
    failed_events: int = 0
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    events_per_second: float = 0.0
    errors: List[str] = None
    
    def __post_init__(self):
        if self.errors is None:
            self.errors = []


class LoadTestSimulator:
    """Simulates concurrent brands publishing events."""
    
    def __init__(self, config: LoadTestConfig, event_bus=None):
        self.config = config
        self.event_bus = event_bus
        self.metrics = LoadTestMetrics()
        self.latencies = []
        self.start_time = None
    
    async def simulate_brand_events(self, brand_id: str, duration: int):
        """Simulate a single brand publishing events."""
        end_time = time.time() + duration
        
        while time.time() < end_time:
            event_type = random.choice(self.config.event_types)
            event_data = self._generate_event(brand_id, event_type)
            
            try:
                start = time.time()
                await self._publish_event(event_type, event_data)
                latency = (time.time() - start) * 1000
                
                self.latencies.append(latency)
                self.metrics.successful_events += 1
            except Exception as e:
                self.metrics.failed_events += 1
                self.metrics.errors.append(str(e))
            
            self.metrics.total_events += 1
            
            # Wait before next event based on config
            await asyncio.sleep(1.0 / self.config.events_per_brand_per_second)
    
    def _generate_event(self, brand_id: str, event_type: str):
        """Generate a realistic event."""
        base = {
            "brand_id": brand_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        if event_type == "engagement_data":
            return {**base, "engagement": random.randint(5, 200)}
        elif event_type == "post_published":
            return {**base, "topic": f"Topic {random.randint(1, 100)}"}
        elif event_type == "strategy_recommendation":
            return {**base, "action": random.choice([
                "increase_similar_content",
                "reduce_underperforming_content",
                "change_posting_time",
            ])}
        elif event_type == "engine_alert":
            return {**base, "engine": "content_engine", "status": "degraded"}
        
        return base
    
    async def _publish_event(self, event_type: str, event_data: dict):
        """Publish event to event bus."""
        if self.event_bus:
            await self.event_bus.publish(event_type, event_data)
        else:
            # Simulate with delay
            await asyncio.sleep(0.001)
    
    async def run(self):
        """Run the load test."""
        print(f"🔥 Starting load test: {self.config.num_concurrent_brands} brands for {self.config.duration_seconds}s")
        self.start_time = time.time()
        
        # Create concurrent tasks for each brand
        tasks = [
            self.simulate_brand_events(f"brand_{i}", self.config.duration_seconds)
            for i in range(self.config.num_concurrent_brands)
        ]
        
        await asyncio.gather(*tasks, return_exceptions=True)
        
        # Calculate metrics
        self._calculate_metrics()
        return self.metrics
    
    def _calculate_metrics(self):
        """Calculate performance metrics."""
        elapsed = time.time() - self.start_time
        
        self.metrics.events_per_second = self.metrics.total_events / elapsed if elapsed > 0 else 0
        
        if self.latencies:
            sorted_latencies = sorted(self.latencies)
            self.metrics.avg_latency_ms = sum(self.latencies) / len(self.latencies)
            self.metrics.p95_latency_ms = sorted_latencies[int(len(sorted_latencies) * 0.95)]
            self.metrics.p99_latency_ms = sorted_latencies[int(len(sorted_latencies) * 0.99)]
    
    def print_summary(self):
        """Print test summary."""
        print("\n" + "="*70)
        print("LOAD TEST SUMMARY")
        print("="*70)
        print(f"Total Events:        {self.metrics.total_events}")
        print(f"Successful:          {self.metrics.successful_events}")
        print(f"Failed:              {self.metrics.failed_events}")
        print(f"Events/sec:          {self.metrics.events_per_second:.2f}")
        print(f"Avg Latency:         {self.metrics.avg_latency_ms:.2f}ms")
        print(f"P95 Latency:         {self.metrics.p95_latency_ms:.2f}ms")
        print(f"P99 Latency:         {self.metrics.p99_latency_ms:.2f}ms")
        if self.metrics.errors:
            print(f"\nErrors ({len(self.metrics.errors)}):")
            for error in self.metrics.errors[:5]:  # Show first 5
                print(f"  - {error}")
        print("="*70 + "\n")


async def run_load_test():
    """Run a standard load test."""
    config = LoadTestConfig(
        num_concurrent_brands=100,
        duration_seconds=60,
        events_per_brand_per_second=2.0,
    )
    
    simulator = LoadTestSimulator(config)
    metrics = await simulator.run()
    simulator.print_summary()
    
    return metrics


if __name__ == "__main__":
    asyncio.run(run_load_test())
