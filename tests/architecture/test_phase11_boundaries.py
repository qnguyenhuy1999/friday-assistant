"""Phase 11 architecture boundaries: the application layer stays free of
subprocess and vendor specifics, Claude lives only in infrastructure/brain,
and no Anthropic SDK or API-key setting exists anywhere."""

from __future__ import annotations

from pathlib import Path

from tests.architecture.test_python_boundaries import imported_modules

REPO_ROOT = Path(__file__).resolve().parents[2]
APPLICATION_ROOT = REPO_ROOT / "src" / "friday" / "application"
DOMAIN_ROOT = REPO_ROOT / "src" / "friday" / "domain"
SOURCE_ROOTS = (REPO_ROOT / "src", REPO_ROOT / "apps")

_FORBIDDEN_IN_APPLICATION = ("subprocess", "anthropic", "claude")


def _python_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def test_application_imports_no_subprocess_or_vendor_modules() -> None:
    offenders: list[str] = []
    for path in _python_files(APPLICATION_ROOT):
        for module in imported_modules(path.read_text(encoding="utf-8")):
            top = module.split(".")[0]
            if top in _FORBIDDEN_IN_APPLICATION:
                offenders.append(f"{path}: imports {module}")
    assert offenders == []


def test_domain_imports_no_subprocess() -> None:
    offenders: list[str] = []
    for path in _python_files(DOMAIN_ROOT):
        for module in imported_modules(path.read_text(encoding="utf-8")):
            if module.split(".")[0] == "subprocess":
                offenders.append(f"{path}: imports {module}")
    assert offenders == []


def test_claude_specific_code_lives_only_in_infrastructure_brain() -> None:
    offenders: list[str] = []
    for path in _python_files(REPO_ROOT / "src" / "friday"):
        if "infrastructure/brain" in str(path):
            continue
        for module in imported_modules(path.read_text(encoding="utf-8")):
            if "claude" in module.lower():
                offenders.append(f"{path}: imports {module}")
    assert offenders == []


def test_no_anthropic_sdk_import_anywhere() -> None:
    offenders: list[str] = []
    for root in SOURCE_ROOTS:
        for path in _python_files(root):
            for module in imported_modules(path.read_text(encoding="utf-8")):
                if module.split(".")[0] == "anthropic":
                    offenders.append(f"{path}: imports {module}")
    assert offenders == []


def test_no_api_key_setting_exists_in_source() -> None:
    """The subscription path is the only auth path: production source never
    reads ANTHROPIC_API_KEY. (The brain adapter mentions it once — in the
    allowlist docstring explaining that it is deliberately dropped.)"""
    offenders: list[str] = []
    for root in SOURCE_ROOTS:
        for path in _python_files(root):
            text = path.read_text(encoding="utf-8")
            if "ANTHROPIC_API_KEY" not in text:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if "ANTHROPIC_API_KEY" in line and "environ" in line:
                    offenders.append(f"{path}:{line_number}: reads ANTHROPIC_API_KEY")
    assert offenders == []


def test_no_computer_use_or_memory_phase_leakage() -> None:
    """Phase 12+ concerns (Obsidian, Graphify, browser/computer use, MCP)
    must not appear in production imports."""
    forbidden = ("obsidian", "graphify", "playwright", "selenium", "mcp")
    offenders: list[str] = []
    for root in SOURCE_ROOTS:
        for path in _python_files(root):
            for module in imported_modules(path.read_text(encoding="utf-8")):
                if module.split(".")[0].lower() in forbidden:
                    offenders.append(f"{path}: imports {module}")
    assert offenders == []
