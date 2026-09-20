"""
Trend Intelligence Plugin v2.0 — Organism-aligned trend capability.

v2.0 (architecture alignment):
  - Staged events: trend.detected → trend.scored → trend.decision.created
    → trend.publish.requested (NO duplicate trend_detected)
  - Workspace-scoped event envelopes + optional workspace filters
  - Durable dedup via DB (SELECT by url / insert); in-memory is soft cache only
  - Async lifecycle preferred; optional background task instead of raw Thread+sleep
  - Publishing decoupled: creates publish job record / emits event — does NOT
    call Facebook or scheduler.trigger_manual() directly
  - Structured logging; failures never swallowed silently without a log

v1.x: prototype scanner + brand routing (retained adapters).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from services.smart_sensor import AdaptiveFrequency, CircuitBreaker

logger = logging.getLogger("kanyoza.plugins.trends")

# ── Event bus ──────────────────────────────────────────────────────────────
try:
    from core.shared_event_bus import get_shared_event_bus
    HAS_EVENT_BUS = True
except ImportError:
    HAS_EVENT_BUS = False


def _publish_event(
    event_name: str,
    payload: Dict[str, Any],
    *,
    workspace_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> None:
    """Publish with workspace-aware envelope fields (best-effort, async-safe)."""
    if not HAS_EVENT_BUS:
        return
    body = dict(payload)
    ws = workspace_id or body.get("workspace_id") or "platform"
    body["workspace_id"] = ws
    body["correlation_id"] = correlation_id or body.get("correlation_id") or uuid.uuid4().hex
    body["emitted_at"] = datetime.now(timezone.utc).isoformat()
    try:
        bus = get_shared_event_bus()
        try:
            result = bus.publish(event_name, body, source="trend_intelligence", workspace_id=ws)
        except TypeError:
            result = bus.publish(event_name, body)
        import asyncio
        import inspect
        if inspect.iscoroutine(result):
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(result)
            except RuntimeError:
                asyncio.run(result)
    except Exception as exc:
        logger.debug("trend event publish failed (%s): %s", event_name, exc)


class TrendIntelligencePlugin:
    """
    Perception + cognition for trends.

    Does NOT own Facebook publishing. Emits publish requests for the
    platform pipeline / Director / PublishService.
    """

    VERSION = "2.4.0"

    def __init__(self, supabase_client=None, redis_client=None):
        self.supabase = supabase_client
        self.redis = redis_client
        self.name = "trend_intelligence"
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._async_task: Optional[asyncio.Task] = None
        self._scan_interval = int(os.environ.get("TREND_SCAN_INTERVAL_SECONDS", "300"))
        self._frequency = AdaptiveFrequency(
            base_interval=self._scan_interval,
            min_interval=int(os.environ.get("TREND_MIN_INTERVAL_SECONDS", "60")),
            max_interval=int(os.environ.get("TREND_MAX_INTERVAL_SECONDS", "3600")),
        )
        self._enabled = True
        self._preflight_complete = False
        self._sources: Dict[str, Dict[str, Any]] = {}
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._last_scan_state = None
        self._started_at = datetime.now(timezone.utc)
        self._metrics = {"scans_total": 0, "scans_skipped": 0, "scans_succeeded": 0, "scans_failed": 0, "items_collected": 0}
        # Soft process cache only — DB is authoritative
        self._seen_urls: Set[str] = set()
        self._lock = threading.RLock()
        self._default_workspace_id = os.environ.get("DEFAULT_WORKSPACE_ID", "platform")

    # ── Plugin API ────────────────────────────────────────────────────────

    def get_commands(self) -> List[Dict[str, Any]]:
        return [
            {"name": "trend_scan", "description": "Scan for trending topics now", "handler": self._handle_scan},
            {"name": "trend_status", "description": "Get trend intelligence status", "handler": self._handle_status},
        ]

    def get_webhooks(self) -> List[Dict[str, Any]]:
        return []

    def start(self):
        if self._running:
            return
        if not self._preflight_sources():
            self._enabled = False
            logger.info("TrendIntelligence: scanner disabled — no configured sources")
            return
        self._enabled = True
        self._running = True
        # Prefer attaching to running asyncio loop; else daemon thread with sleep
        try:
            loop = asyncio.get_running_loop()
            self._async_task = loop.create_task(self._async_loop(), name="trend-engine")
            logger.info(
                "TrendIntelligence v%s: started async (scan every %ds)",
                self.VERSION,
                self._scan_interval,
            )
        except RuntimeError:
            self._thread = threading.Thread(
                target=self._thread_loop, daemon=True, name="trend-engine"
            )
            self._thread.start()
            logger.info(
                "TrendIntelligence v%s: started thread fallback (scan every %ds)",
                self.VERSION,
                self._scan_interval,
            )
        self._subscribe_publish_bridge()

    def _subscribe_publish_bridge(self) -> None:
        """Compatibility hook; publishing is intentionally event-driven in v2+."""
        # Trend Intelligence emits staged trend/publish-request events. It must
        # not directly own a publisher subscription, so this is deliberately
        # idempotent and side-effect free.
        if not getattr(self, "_publish_bridge_initialized", False):
            self._publish_bridge_initialized = True
            logger.info("TrendIntelligence publish bridge: event-driven mode")

    def stop(self):
        self._running = False
        if self._async_task and not self._async_task.done():
            self._async_task.cancel()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("TrendIntelligence: stopped")

    async def _async_loop(self):
        while self._running and self._enabled:
            try:
                await asyncio.get_event_loop().run_in_executor(None, self._cycle)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("TrendIntelligence cycle error: %s", e)
            try:
                await asyncio.sleep(self._frequency.current_interval)
            except asyncio.CancelledError:
                break

    def _thread_loop(self):
        while self._running and self._enabled:
            try:
                self._cycle()
            except Exception as e:
                logger.error("TrendIntelligence cycle error: %s", e)
            # Interruptible sleep
            for _ in range(self._frequency.current_interval):
                if not self._running:
                    break
                time.sleep(1)

    def _preflight_sources(self) -> bool:
        """Load enabled sources once and exclude unconfigured sources before scheduling."""
        if self._preflight_complete:
            return bool(self._sources)
        if not self.supabase:
            logger.info("TrendIntelligence: disabled — no data source configured")
            self._preflight_complete = True
            return False
        try:
            response = (
                self.supabase.table("trend_sources")
                .select("*")
                .eq("enabled", True)
                .execute()
            )
        except Exception as exc:
            # Infrastructure failure is not the same as an unconfigured sensor.
            logger.error("TrendIntelligence: source preflight unavailable: %s", exc)
            return True

        configured = 0
        for source in response.data or []:
            platform = (source.get("platform") or "").lower().strip()
            name = source.get("name") or source.get("id") or platform
            query = source.get("query") or (source.get("config") or {}).get("query") or ""
            try:
                from plugins.trend_intelligence.adapters import get_adapter
                adapter = get_adapter(platform)
            except Exception:
                adapter = None
            is_configured = bool(adapter and adapter.is_configured())
            if platform in {"rss", "feed", "atom"}:
                is_configured = bool(query.strip().startswith(("http://", "https://")))
            if not is_configured:
                logger.info("TrendIntelligence: source '%s' disabled (not configured)", name)
                continue
            self._sources[str(source.get("id") or name)] = source
            configured += 1

        self._preflight_complete = True
        return configured > 0

    def _cycle(self):
        self.scan_all_sources()
        self.process_trends()

    # ── Scanning ──────────────────────────────────────────────────────────

    def scan_all_sources(self):
        if not self.supabase or not self._enabled:
            return None
        if not self._preflight_sources():
            self._metrics["scans_skipped"] += 1
            return {"items_fetched": 0, "sources": 0}
        t0 = time.monotonic()
        _publish_event(
            "trend.scan.started",
            {"interval_s": self._scan_interval},
            workspace_id=self._default_workspace_id,
        )
        try:
            # Preflight is the configuration gate; DB remains authoritative for source rows.
            sources = type("SourceResponse", (), {"data": list(self._sources.values())})()
        except Exception as e:
            logger.error("TrendIntelligence: failed to load sources: %s", e)
            _publish_event(
                "trend.scan.failed",
                {"error": str(e)[:200]},
                workspace_id=self._default_workspace_id,
            )
            return

        total_fetched = 0
        total_new = 0
        checked = succeeded = skipped = failed = 0
        self._metrics["scans_total"] += 1
        for source in sources.data or []:
            source_id = str(source.get("id") or source.get("name"))
            workspace_id = source.get("workspace_id") or self._default_workspace_id
            name = source.get("name") or source.get("id")
            breaker = self._breakers.setdefault(source_id, CircuitBreaker())
            if not breaker.can_attempt():
                skipped += 1
                continue
            checked += 1
            try:
                posts = self._fetch_from_source(source)
                new_posts = self._store_posts(source_id=source_id, posts=posts, workspace_id=workspace_id, source_name=name)
                total_fetched += len(posts)
                total_new += new_posts
                if posts:
                    succeeded += 1
                    breaker.record_success()
                else:
                    skipped += 1
                    # Empty is idle, not a failure.
                    breaker.record_success()
                try:
                    self.supabase.table("trend_sources").update({"last_fetched": datetime.now(timezone.utc).isoformat()}).eq("id", source_id).execute()
                except Exception as e:
                    logger.debug("TrendIntelligence: last_fetched update failed: %s", e)
                _publish_event("trend.source.fetched", {"source_id": source_id, "source_name": name, "items_fetched": len(posts), "items_new": new_posts}, workspace_id=workspace_id)
            except Exception as e:
                failed += 1
                previous_state = breaker.state
                breaker.record_failure()
                if breaker.state == "open" and previous_state != "open":
                    logger.warning("TrendIntelligence: source '%s' circuit opened after %d failures", name, breaker.failures)
                logger.warning("TrendIntelligence: source '%s' failed: %s", name, str(e)[:200])
                _publish_event("trend.source.failed", {"source_id": source_id, "source_name": name, "error": str(e)[:200]}, workspace_id=workspace_id)

        self._metrics["items_collected"] += total_new
        self._metrics["scans_succeeded"] += succeeded
        self._metrics["scans_failed"] += failed
        state = (succeeded, failed, skipped, len(sources.data or []))
        if state != self._last_scan_state:
            logger.info("TrendIntelligence scan: %d/%d sources returned %d items (failed: %d, skipped: %d)", succeeded, checked, total_fetched, failed, skipped)
            self._last_scan_state = state
        else:
            logger.debug("TrendIntelligence scan: no state change")

        result = {
            "sources": len(sources.data or []),
            "checked": checked,
            "succeeded": succeeded,
            "failed": failed,
            "skipped": skipped,
            "items_fetched": total_fetched,
            "items_new": total_new,
            "duration_ms": round((time.monotonic() - t0) * 1000, 1),
        }
        _publish_event("trend.scan.completed", result, workspace_id=self._default_workspace_id)
        self._frequency.on_result(total_fetched > 0)
        return result

    def _fetch_from_source(self, source: dict) -> List[dict]:
        platform = (source.get("platform") or "").lower().strip()
        query = source.get("query") or (source.get("config") or {}).get("query") or ""
        try:
            from plugins.trend_intelligence.adapters import get_adapter
            adapter = get_adapter(platform)
        except Exception as e:
            logger.debug("Adapter import failed, using legacy: %s", e)
            adapter = None

        if adapter is not None:
            if not adapter.is_configured():
                logger.debug("TrendIntelligence: source '%s' skipped — not configured", source.get("name"))
                return []
            trends = adapter.fetch(query)
            return [t.to_dict() if hasattr(t, "to_dict") else t for t in trends]

        # Legacy fallback (should not be needed)
        logger.warning(
            "TrendIntelligence: unknown platform %r for source %s",
            platform,
            source.get("name"),
        )
        return []

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self._enabled,
            "running": self._running,
            "preflight_complete": self._preflight_complete,
            "sources_configured": len(self._sources),
            "scans_total": self._metrics["scans_total"],
            "scans_skipped": self._metrics["scans_skipped"],
            "scans_succeeded": self._metrics["scans_succeeded"],
            "scans_failed": self._metrics["scans_failed"],
            "items_collected": self._metrics["items_collected"],
            "current_interval_seconds": self._frequency.current_interval,
            "last_scan_state": self._last_scan_state,
            "breakers": {k: v.status() for k, v in self._breakers.items()},
        }

    def _store_posts(
        self,
        source_id: str,
        posts: List[dict],
        *,
        workspace_id: str,
        source_name: str = "",
    ) -> int:
        """Durable dedup via DB; emit trend.detected only for new inserts."""
        new_count = 0
        for post in posts:
            url = (post.get("url") or "").strip()
            if not url:
                continue
            # Soft cache (process only)
            with self._lock:
                if url in self._seen_urls:
                    _publish_event(
                        "trend.duplicate",
                        {"url": url, "source_id": source_id},
                        workspace_id=workspace_id,
                    )
                    continue
            try:
                existing = (
                    self.supabase.table("trend_posts")
                    .select("id")
                    .eq("url", url)
                    .limit(1)
                    .execute()
                )
                if existing.data:
                    with self._lock:
                        self._seen_urls.add(url)
                    _publish_event(
                        "trend.duplicate",
                        {"url": url, "source_id": source_id},
                        workspace_id=workspace_id,
                    )
                    continue

                row = {
                    "source_id": source_id,
                    "title": post.get("title") or "",
                    "content": post.get("content") or "",
                    "url": url,
                    "author": post.get("author") or "",
                    "platform": post.get("platform") or "unknown",
                    "engagement_count": post.get("engagement_count") or 0,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
                # Best-effort workspace column if schema supports it
                if workspace_id and workspace_id != "platform":
                    row["workspace_id"] = workspace_id

                inserted = self.supabase.table("trend_posts").insert(row).execute()
                new_count += 1
                with self._lock:
                    self._seen_urls.add(url)
                    if len(self._seen_urls) > 2000:
                        # Bound soft cache without relying on set order
                        self._seen_urls = set(list(self._seen_urls)[:1000])

                trend_id = None
                if inserted and inserted.data:
                    trend_id = inserted.data[0].get("id")

                correlation_id = uuid.uuid4().hex
                _publish_event(
                    "trend.detected",
                    {
                        "trend_id": trend_id,
                        "topic": post.get("title") or "",
                        "source": post.get("platform") or "unknown",
                        "source_id": source_id,
                        "source_name": source_name,
                        "url": url,
                        "author": post.get("author") or "",
                        "engagement_count": post.get("engagement_count") or 0,
                        "aggregate_id": trend_id or url,
                    },
                    workspace_id=workspace_id,
                    correlation_id=correlation_id,
                )
            except Exception as e:
                # Unique violation = concurrent insert = treat as duplicate
                msg = str(e).lower()
                if "unique" in msg or "duplicate" in msg:
                    with self._lock:
                        self._seen_urls.add(url)
                    _publish_event(
                        "trend.duplicate",
                        {"url": url, "source_id": source_id, "race": True},
                        workspace_id=workspace_id,
                    )
                else:
                    logger.warning("TrendIntelligence: store error: %s", e)
        return new_count

    # ── Intelligence / decisions ──────────────────────────────────────────


    def _diagnose_zero_activity(self) -> None:
        """Loud, actionable reasons when pipeline is silent."""
        import os
        reasons = []
        if not self.supabase:
            reasons.append("supabase_client_missing")
        if not os.environ.get("NEWSAPI_KEY", "").strip():
            reasons.append("NEWSAPI_KEY not set")
        if not (
            os.environ.get("TWITTER_BEARER_TOKEN") or os.environ.get("TWITTER_TOKEN") or ""
        ).strip():
            reasons.append("TWITTER_BEARER_TOKEN not set")
        if not os.environ.get("TAVILY_API_KEY", "").strip():
            reasons.append("TAVILY_API_KEY not set (optional web search)")
        try:
            if self.supabase:
                src = self.supabase.table("trend_sources").select("id,platform,enabled").limit(20).execute()
                rows = src.data or []
                if not rows:
                    reasons.append("no trend_sources rows in Supabase")
                else:
                    enabled = [r for r in rows if r.get("enabled") is not False]
                    if not enabled:
                        reasons.append("all trend_sources disabled")
                    platforms = {str(r.get("platform") or "").lower() for r in enabled}
                    reasons.append(f"enabled_platforms={sorted(platforms)}")
        except Exception as e:
            reasons.append(f"trend_sources_query_failed:{e}")
        logger.warning(
            "TrendIntelligence DIAG: pipeline quiet — %s",
            "; ".join(reasons) or "unknown",
        )

    def process_trends(self):
        if not self.supabase:
            return
        try:
            rules_q = (
                self.supabase.table("trend_brand_rules")
                .select("*")
                .eq("enabled", True)
            )
            if self._default_workspace_id and self._default_workspace_id != "platform":
                try:
                    rules_q = rules_q.eq("workspace_id", self._default_workspace_id)
                except Exception:
                    pass
            rules = rules_q.execute()
            trends_q = (
                self.supabase.table("trend_posts")
                .select("*")
                .order("fetched_at", desc=True)
                .limit(20)
            )
            if self._default_workspace_id and self._default_workspace_id != "platform":
                try:
                    trends_q = trends_q.eq("workspace_id", self._default_workspace_id)
                except Exception:
                    pass
            trends = trends_q.execute()
        except Exception as e:
            logger.error("TrendIntelligence: process_trends load failed: %s", e)
            return

        if not (trends.data or []):
            self._diagnose_zero_activity()
        for trend in trends.data or []:
            for rule in rules.data or []:
                # Workspace isolation when both sides have workspace_id
                tw = trend.get("workspace_id")
                rw = rule.get("workspace_id")
                if tw and rw and tw != rw:
                    continue
                try:
                    existing = (
                        self.supabase.table("trend_decisions")
                        .select("id")
                        .eq("trend_post_id", trend["id"])
                        .eq("brand_id", rule["brand_id"])
                        .limit(1)
                        .execute()
                    )
                    if existing.data:
                        continue
                    relevance = self._calculate_relevance(trend, rule)
                    relevance = self._maybe_web_verify(trend, rule, relevance)
                    workspace_id = (
                        rw or tw or rule.get("workspace_id") or self._default_workspace_id
                    )
                    correlation_id = uuid.uuid4().hex
                    _publish_event(
                        "trend.scored",
                        {
                            "trend_id": trend["id"],
                            "brand_id": rule["brand_id"],
                            "relevance": relevance,
                            "topic": trend.get("title"),
                        },
                        workspace_id=workspace_id,
                        correlation_id=correlation_id,
                    )
                    decision = self._make_decision(trend, rule, relevance)
                    self._execute_decision(
                        trend, rule, decision, relevance, workspace_id, correlation_id
                    )
                except Exception as e:
                    logger.error(
                        "TrendIntelligence: decision failed trend=%s brand=%s: %s",
                        trend.get("id"),
                        rule.get("brand_id"),
                        e,
                    )


    def _maybe_web_verify(self, trend: dict, rule: dict, relevance: float) -> float:
        """Optional cross-check with Web Intelligence (TREND_WEB_VERIFY=true)."""
        import os
        if os.environ.get("TREND_WEB_VERIFY", "").lower() not in ("1", "true", "yes"):
            return relevance
        try:
            from services.web_intelligence import get_web_intelligence, init_web_intelligence
            gw = get_web_intelligence() or init_web_intelligence()
            q = (trend.get("title") or "")[:120]
            if not q:
                return relevance
            results = gw.search(q, max_results=3)
            if not results:
                return max(0.0, relevance - 5)  # no corroboration soft penalty
            # boost if other sources mention similar title words
            words = set(w.lower() for w in q.split() if len(w) > 3)
            hits = 0
            for r in results:
                blob = f"{r.title} {r.snippet}".lower()
                if sum(1 for w in words if w in blob) >= max(1, len(words) // 3):
                    hits += 1
            if hits >= 2:
                return min(100.0, relevance + 12)
            if hits == 1:
                return min(100.0, relevance + 5)
            return relevance
        except Exception as exc:
            logger.debug("web verify skipped: %s", exc)
            return relevance

    def _calculate_relevance(self, trend: dict, rule: dict) -> float:
        """
        Composite score (0-100):
          ~45% keyword relevance
          ~20% engagement velocity
          ~15% freshness (if published_at/fetched_at present)
          ~10% source credibility heuristic
          ~10% rule weight boost
        """
        keywords = rule.get("keywords") or []
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",") if k.strip()]
        body = f"{trend.get('title', '')} {trend.get('content', '')}".lower()

        # Keyword relevance (0-45)
        if keywords:
            matches = sum(1 for k in keywords if str(k).lower() in body)
            keyword_score = (matches / max(len(keywords), 1)) * 45.0
        else:
            keyword_score = 22.5  # neutral when no keywords

        # Engagement (0-20)
        eng = int(trend.get("engagement_count") or 0)
        if eng >= 5000:
            eng_score = 20.0
        elif eng >= 1000:
            eng_score = 15.0
        elif eng >= 100:
            eng_score = 10.0
        elif eng >= 10:
            eng_score = 5.0
        else:
            eng_score = 0.0

        # Freshness (0-15)
        fresh_score = 8.0  # default mid
        for key in ("published_at", "fetched_at"):
            raw = trend.get(key)
            if not raw:
                continue
            try:
                ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
                if hours <= 6:
                    fresh_score = 15.0
                elif hours <= 24:
                    fresh_score = 12.0
                elif hours <= 72:
                    fresh_score = 8.0
                else:
                    fresh_score = 3.0
            except Exception:
                pass
            break

        # Source credibility heuristic (0-10)
        platform = (trend.get("platform") or "").lower()
        credibility = {
            "newsapi": 9.0,
            "rss": 7.0,
            "reddit": 6.0,
            "twitter": 5.0,
            "x": 5.0,
        }.get(platform, 5.0)

        # Rule weight (0-10)
        weight = float(rule.get("weight") or rule.get("priority") or 5.0)
        weight_score = max(0.0, min(10.0, weight))

        total = keyword_score + eng_score + fresh_score + credibility + weight_score

        # Optional semantic nudge (non-blocking budget — only if enabled)
        try:
            import os
            if os.environ.get("TREND_SEMANTIC_SCORE", "").lower() in ("1", "true", "yes"):
                semantic = self._semantic_relevance(body, keywords)
                if semantic is not None:
                    # blend: 70% heuristic, 30% semantic
                    total = total * 0.7 + float(semantic) * 0.3
        except Exception:
            pass

        return round(min(100.0, total), 2)

    def _semantic_relevance(self, text: str, keywords: list) -> float | None:
        """Optional semantic score via LLM Gateway — fail soft, never invent scores."""
        try:
            from ai.gateway import get_llm_gateway
            prompt = (
                "Score relevance of this content to the brand keywords on a scale 0-100. "
                "Reply with ONLY a number.\n"
                f"Keywords: {', '.join(str(k) for k in (keywords or [])[:20])}\n"
                f"Content: {(text or '')[:800]}\n"
                "Score:"
            )
            result = get_llm_gateway().complete(
                prompt,
                workspace_id=getattr(self, "_current_workspace_id", None) or "platform",
                capability="chat",
                temperature=0.1,
                max_tokens=16,
                caller="trend",
            )
            if not result.ok or not result.text:
                return None  # heuristic path continues without fake score
            import re as _re
            m2 = _re.search(r"(\d{1,3})", str(result.text))
            if not m2:
                return None
            return max(0.0, min(100.0, float(m2.group(1))))
        except Exception:
            return None


    def _make_decision(self, trend: dict, rule: dict, relevance: float) -> str:
        threshold = float(rule.get("min_relevance") or rule.get("threshold") or 40)
        mode = (rule.get("action") or rule.get("mode") or "auto").lower()
        if relevance < threshold:
            return "skip"
        if mode in ("manual", "approve", "review"):
            return "review"
        if mode in ("schedule", "scheduled"):
            return "schedule"
        return "publish"

    def _execute_decision(
        self,
        trend: dict,
        rule: dict,
        decision: str,
        relevance: float,
        workspace_id: str,
        correlation_id: str,
    ):
        brand_id = rule["brand_id"]
        try:
            self.supabase.table("trend_decisions").insert({
                "trend_post_id": trend["id"],
                "brand_id": brand_id,
                "decision": decision,
                "relevance_score": relevance,
                "decided_at": datetime.now(timezone.utc).isoformat(),
                "workspace_id": workspace_id,
            }).execute()
        except Exception as e:
            logger.warning("TrendIntelligence: decision persist failed: %s", e)

        _publish_event(
            "trend.decision.created",
            {
                "trend_id": trend["id"],
                "brand_id": brand_id,
                "decision": decision,
                "relevance": relevance,
                "topic": trend.get("title"),
                "url": trend.get("url"),
            },
            workspace_id=workspace_id,
            correlation_id=correlation_id,
        )

        if decision == "skip":
            return

        if decision == "review":
            logger.info(
                "TrendIntelligence: review required brand=%s topic=%s score=%.1f",
                brand_id,
                (trend.get("title") or "")[:60],
                relevance,
            )
            return

        if decision in ("publish", "schedule"):
            if not self._within_daily_limit(brand_id, rule):
                logger.info(
                    "TrendIntelligence: daily limit reached brand=%s — skip publish",
                    brand_id,
                )
                _publish_event(
                    "trend.decision.created",
                    {
                        "trend_id": trend["id"],
                        "brand_id": brand_id,
                        "decision": "skipped_daily_limit",
                        "relevance": relevance,
                    },
                    workspace_id=workspace_id,
                    correlation_id=correlation_id,
                )
                return
            self._request_publish(
                trend=trend,
                brand_id=brand_id,
                decision=decision,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
                relevance=relevance,
            )

    def _within_daily_limit(self, brand_id: str, rule: dict) -> bool:
        """
        Daily cap with best-effort distributed lock.

        When Redis is available, acquires trend:daily:{brand}:{date} briefly
        around the count check to reduce double-publish races.
        UNIQUE(trend_post_id, brand_id) on trend_decisions is the hard guarantee.
        """
        max_posts = int(rule.get("max_posts_per_day") or rule.get("daily_limit") or 0)
        if max_posts <= 0:
            return True
        if not self.supabase:
            return True

        lock = None
        try:
            from core.distributed_lock import DistributedLock
            redis = getattr(self, "redis", None) or getattr(self, "redis_client", None)
            from datetime import date
            today = date.today().isoformat()
            lock = DistributedLock(
                f"trend:daily:{brand_id}:{today}",
                redis_client=redis,
                timeout_seconds=15.0,
                allow_local_fallback=True,
            )
            if not lock.acquire(require_distributed=False):
                logger.debug("daily limit lock not acquired brand=%s — denying publish this tick", brand_id)
                return False
        except Exception as exc:
            logger.debug("daily limit lock soft-fail: %s", exc)
            lock = None

        try:
            from datetime import date
            today = date.today().isoformat()
            q = (
                self.supabase.table("trend_decisions")
                .select("id")
                .eq("brand_id", brand_id)
                .in_("decision", ["publish", "schedule"])
                .gte("decided_at", f"{today}T00:00:00")
                .limit(max_posts + 5)
                .execute()
            )
            count = len(q.data or [])
            return count < max_posts
        except Exception as e:
            logger.debug("daily limit check failed (allowing): %s", e)
            return True
        finally:
            if lock is not None:
                try:
                    lock.release()
                except Exception:
                    pass

    def _brief_for_social(self, *, topic: str, content: str, brand_id: str) -> Optional[str]:
        """Rewrite trend via LLM Gateway when TREND_LLM_BRIEFING is enabled.

        On unavailable/degraded returns None — deterministic content path continues.
        """
        import os
        if os.environ.get("TREND_LLM_BRIEFING", "").lower() not in ("1", "true", "yes"):
            return None
        try:
            from ai.gateway import get_llm_gateway
            prompt = (
                "You write concise Facebook posts for a tech brand.\n"
                "Rewrite the trend below into one engaging post (max 280 words).\n"
                "Include 2-4 relevant hashtags. No clickbait. No invented facts.\n"
                f"Brand id: {brand_id}\n"
                f"Title: {topic[:200]}\n"
                f"Source text: {(content or '')[:1200]}\n\n"
                "Post:"
            )
            result = get_llm_gateway().complete(
                prompt,
                workspace_id=getattr(self, "_current_workspace_id", None) or "platform",
                capability="chat",
                temperature=0.6,
                max_tokens=400,
                caller="trend",
            )
            if result.ok and result.text and len(result.text.strip()) > 40:
                return result.text.strip()[:2000]
            logger.debug(
                "LLM briefing degraded mode=%s reason=%s",
                getattr(result, "mode", None),
                getattr(result, "reason", ""),
            )
        except Exception as exc:
            logger.debug("LLM briefing skipped: %s", exc)
        return None


    def _request_publish(

        self,
        *,
        trend: dict,
        brand_id: str,
        decision: str,
        workspace_id: str,
        correlation_id: str,
        relevance: float,
    ):
        """
        Decoupled publish path: emit job request + optional outbox row.
        Does NOT call Facebook or scheduler.trigger_manual().
        """
        topic = trend.get("title") or ""
        raw_content = trend.get("content") or topic
        briefed = self._brief_for_social(topic=topic, content=raw_content, brand_id=brand_id)
        post_body = briefed or raw_content

        job = {
            "job_id": f"trendpub_{uuid.uuid4().hex[:12]}",
            "workspace_id": workspace_id,
            "brand_id": brand_id,
            "source": "trend_intelligence",
            "trend_id": trend.get("id"),
            "topic": topic,
            "content": post_body,
            "url": trend.get("url"),
            "platform": trend.get("platform"),
            "decision": decision,  # publish | schedule
            "relevance": relevance,
            "correlation_id": correlation_id,
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "status": "requested",
            "briefed": bool(briefed),
        }

        # Soft image hint via Cloudflare module if available (non-blocking)
        try:
            from services.cloudflare_image import generate_trend_image, is_configured
            if is_configured():
                image_url = generate_trend_image({
                    "title": trend.get("title") or "",
                    "content": trend.get("content") or "",
                    "platform": "facebook",
                })
                if image_url:
                    job["media_url"] = image_url
                    _publish_event(
                        "trend.content.generated",
                        {
                            "trend_id": trend.get("id"),
                            "brand_id": brand_id,
                            "media_url": image_url[:200],
                        },
                        workspace_id=workspace_id,
                        correlation_id=correlation_id,
                    )
        except Exception as e:
            logger.debug("Trend image generation skipped: %s", e)

        # Persist publish request if table exists
        try:
            self.supabase.table("trend_publish_jobs").insert(job).execute()
        except Exception as e:
            logger.debug(
                "trend_publish_jobs insert skipped (table may not exist): %s", e
            )

        _publish_event(
            "trend.publish.requested",
            job,
            workspace_id=workspace_id,
            correlation_id=correlation_id,
        )
        logger.info(
            "TrendIntelligence: publish requested job=%s brand=%s decision=%s topic=%s",
            job["job_id"],
            brand_id,
            decision,
            (trend.get("title") or "")[:60],
        )

    # ── Commands ──────────────────────────────────────────────────────────

    def _handle_scan(self, params: dict) -> dict:
        self.scan_all_sources()
        self.process_trends()
        return {"ok": True, "message": "Trend scan triggered", "version": self.VERSION}

    def _handle_status(self, params: dict) -> dict:
        if not self.supabase:
            return {"ok": False, "error": "Database unavailable"}
        try:
            sources = self.supabase.table("trend_sources").select("*").execute()
            recent = (
                self.supabase.table("trend_posts")
                .select("*")
                .order("fetched_at", desc=True)
                .limit(10)
                .execute()
            )
            decisions = (
                self.supabase.table("trend_decisions")
                .select("*")
                .order("decided_at", desc=True)
                .limit(20)
                .execute()
            )
            return {
                "ok": True,
                "version": self.VERSION,
                "sources": len(sources.data or []),
                "recent_trends": recent.data or [],
                "recent_decisions": decisions.data or [],
                "seen_urls_cache": len(self._seen_urls),
                "architecture": "v2_decoupled_publish_staged_events",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}


trend_plugin: Optional[TrendIntelligencePlugin] = None


def init_trend_plugin(supabase_client=None, redis_client=None) -> TrendIntelligencePlugin:
    global trend_plugin
    if trend_plugin is None:
        trend_plugin = TrendIntelligencePlugin(supabase_client, redis_client)
        trend_plugin.start()
    return trend_plugin
