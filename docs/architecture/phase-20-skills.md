# Phase 20 Step 1: versioned Skill registry

Skills are durable behavioral knowledge, not authority. A skill has a stable key and independently auditable immutable revisions. Revisions preserve exact UTF-8 instruction content and its SHA-256; changing behavior requires a new monotonic version.

Friday-owned use cases create revisions and explicitly activate a persisted revision only after confirming ownership. Disabling preserves history; archive is terminal and fences further revisions and activation. SQLite enforces unique keys and `(skill_id, version)`, positive versions, source kinds, lifecycle values, and hash shape. Persistence conflicts are translated to `EntityConflict` by the Unit of Work. The database is the final ownership fence: `skills(id, active_revision_id)` references `skill_revisions(skill_id, id)` (a composite FK guarded by `UNIQUE(skill_id, id)`), so a pointer to a nonexistent revision or to another skill's revision is rejected even if the domain check is bypassed.

Step 1 has no prompt assembly, retrieval, selection, ToolGateway, approval, retry, or scheduling integration. Registering or activating a Skill has zero runtime effect. Later self-improvement can create candidates only by crossing this explicit activation boundary; no generated revision or auto-promotion exists here.
