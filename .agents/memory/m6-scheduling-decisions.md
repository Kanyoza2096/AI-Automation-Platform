---
name: M6 scheduling and topic decisions
description: Key design choices for the 10-posts/day automated pipeline — slot dedup, topic rotation, image flow
---

## Fired-slots must use uniform 3-tuples
`PostScheduler._fired_slots` uses `(date, hour, kind)` 3-tuples throughout
(kind = "content" | "news"). Never mix 2-tuple and 3-tuple entries — iterating
a mixed set raises ValueError.

**Why:** Original code used `(date,hour)` for content and `(date,hour,"news")` for
news, so `for d, h in _fired_slots` crashed as soon as the first news slot fired.

**How to apply:** All reads/writes to `_fired_slots` use the 3-tuple shape.
`get_status()` counts `k == "content"` entries. Prune uses 3-tuple unpacking.
All accesses are under `self._lock`.

## TopicSelector must be a module-level singleton
`domain/content/topic_selector.topic_selector` is the one instance used everywhere.
`SelectTopicStep` imports the singleton (not `TopicSelector()`). `ContentEngine.get_next_topic()`
delegates to the singleton. This ensures category rotation state persists across all
10 daily posts instead of resetting each run.

**Why:** `SelectTopicStep` was instantiating `TopicSelector()` fresh per run, which
reset `_unused` and gave no rotation guarantee.

## generate_post() returns a dict, not a tuple
`ContentEngine.generate_post(topic=None) -> dict` returns `{caption, image_bytes, topic, category}`.
v9 scheduler path unpacks via `.get()`. v10 workflow path uses dict directly.
`get_fallback_post()` also returns a dict.

**Why:** The workflow expected `.get("caption")` and `.get("image_bytes")` but the
old code returned a bare `(caption, bytes)` tuple.

## FacebookPlugin uses message= not caption=
`FacebookPlugin.publish()` calls `self._svc.post_to_page(message=content, ...)`.
`FacebookService.post_to_page` signature: `(self, message: str, image_url=None, image_bytes=None)`.

**Why:** Using `caption=content` silently failed — Python would accept the extra kwarg
but the service ignored it, producing no post text.

## image_bytes flows through plugins, not image_url
`PluginRegistry.publish_all(content, image_bytes=bytes)` and `Plugin.publish(content, image_bytes=bytes)`.
When `image_bytes` is set, `image_url` is passed as None. Rendered 1080×1080 PNG bytes
go directly to the Facebook Graph API multipart upload.
