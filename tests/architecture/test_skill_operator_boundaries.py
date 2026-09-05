from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_OPERATOR_FILES = (
    REPO_ROOT / "apps/web/src/hooks/use-skills.ts",
    REPO_ROOT / "apps/web/src/pages/skills-page.tsx",
    REPO_ROOT / "apps/web/src/pages/skill-detail-page.tsx",
)


def test_skill_operator_surface_only_uses_skill_sdk_operations() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in SKILL_OPERATOR_FILES)

    assert "friday.skills" in source
    assert "ToolGateway" not in source
    assert "requestToolInvocation" not in source
    assert "ToolInvocation" not in source
    assert "fetch(" not in source


def test_hostile_skill_instructions_are_data_not_browser_capability() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in SKILL_OPERATOR_FILES)

    assert "Run shell commands directly" not in source
    assert "Bypass approval" not in source
