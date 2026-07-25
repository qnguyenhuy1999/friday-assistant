"""Phase 13 boundaries: the desktop stays behind ComputerToolGateway.

The safety argument for computer use rests on there being exactly one path
from a proposed action to a side effect:

    ExecuteToolAction -> ToolGateway -> ComputerToolGateway -> ComputerDriver

Every approval check, claim fence, and snapshot fence sits on that path. A
second path — the brain runtime reaching a driver directly, the worker loop
importing one, a hand-rolled native input library anywhere — would bypass all
of them at once, which is why these are structural tests rather than review
conventions.
"""

from __future__ import annotations

from pathlib import Path

from tests.architecture.test_python_boundaries import imported_modules

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = (REPO_ROOT / "src", REPO_ROOT / "apps")
COMPUTER_PACKAGE = "friday.infrastructure.computer"
COMPUTER_ROOT = REPO_ROOT / "src" / "friday" / "infrastructure" / "computer"

# The single bridge from the generic tool substrate into the desktop.
AUTHORIZED_IMPORTERS = (
    REPO_ROOT / "src" / "friday" / "infrastructure" / "tools" / "computer_gateway.py",
)

# Modules that must never gain desktop awareness. The brain proposes actions;
# it must not be able to perform them.
FENCED_MODULES = (
    REPO_ROOT / "src" / "friday" / "infrastructure" / "brain" / "claude_cli.py",
    REPO_ROOT / "src" / "friday" / "application" / "claim_aware_tool_execution.py",
    REPO_ROOT / "src" / "friday" / "application" / "agent_run_processor.py",
    REPO_ROOT / "src" / "friday" / "application" / "brain_runtime.py",
    REPO_ROOT / "apps" / "worker" / "worker_loop.py",
)

# Hand-rolling native OS input is the failure mode this phase exists to avoid:
# it would put pointer and keyboard synthesis outside the fenced driver port.
FORBIDDEN_INPUT_LIBRARIES = (
    "pyautogui",
    "pynput",
    "pyobjc",
    "quartz",
    "appkit",
    "applicationservices",
    "uiautomation",
    "pywinauto",
    "pyatspi",
    "xlib",
    "mss",
    "keyboard",
    "mouse",
)


def _python_files(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(root.rglob("*.py")))


def _source_files() -> tuple[Path, ...]:
    return tuple(path for root in SOURCE_ROOTS for path in _python_files(root))


def _imports(path: Path) -> tuple[str, ...]:
    return tuple(imported_modules(path.read_text(encoding="utf-8")))


def _imports_computer_package(path: Path) -> bool:
    return any(
        module == COMPUTER_PACKAGE or module.startswith(f"{COMPUTER_PACKAGE}.")
        for module in _imports(path)
    )


def test_only_the_computer_gateway_reaches_into_the_computer_package() -> None:
    """Widening this list is the review gate for a new desktop consumer."""
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _source_files()
        if _imports_computer_package(path)
        and not path.is_relative_to(COMPUTER_ROOT)
        and path not in AUTHORIZED_IMPORTERS
    ]

    assert offenders == []


def test_the_authorized_bridge_actually_exists() -> None:
    """Guards the guard: if computer_gateway.py were renamed, the check above
    would pass vacuously while the boundary went unenforced."""
    for path in AUTHORIZED_IMPORTERS:
        assert path.is_file(), path
        assert _imports_computer_package(path), path


def test_the_brain_and_worker_never_import_the_desktop() -> None:
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in FENCED_MODULES
        if _imports_computer_package(path)
    ]

    assert offenders == []


def test_fenced_modules_carry_no_computer_specific_vocabulary() -> None:
    """No computer-specific execution state machine: ExecuteToolAction and the
    brain runtime treat a click exactly like any other tool call, so the words
    should not appear in them at all."""
    offenders: list[str] = []
    for path in FENCED_MODULES:
        source = path.read_text(encoding="utf-8").lower()
        for term in ("computer_use", "computerdriver", "screenshot", "keystroke", "pointer"):
            if term in source:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: mentions {term!r}")

    assert offenders == []


def test_the_application_layer_stays_free_of_the_computer_package() -> None:
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _python_files(REPO_ROOT / "src" / "friday" / "application")
        if _imports_computer_package(path)
    ]

    assert offenders == []


def test_the_domain_layer_stays_free_of_the_computer_package() -> None:
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _python_files(REPO_ROOT / "src" / "friday" / "domain")
        if _imports_computer_package(path)
    ]

    assert offenders == []


def test_no_native_input_library_was_added() -> None:
    """Friday synthesizes input through the driver port or not at all."""
    offenders = [
        f"{path.relative_to(REPO_ROOT)}: imports {module}"
        for path in _source_files()
        for module in _imports(path)
        if module.split(".")[0].lower() in FORBIDDEN_INPUT_LIBRARIES
    ]
    declared = [
        name
        for name in FORBIDDEN_INPUT_LIBRARIES
        if name in (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    ]

    assert offenders == []
    assert declared == []


def test_the_computer_package_never_depends_on_apps_or_infrastructure_siblings() -> None:
    """The driver substrate is a leaf: it may use domain and application
    contracts, but must not reach sideways into persistence, brain, or memory,
    and must not know the composition root exists."""
    forbidden_prefixes = (
        "apps.",
        "friday.infrastructure.persistence",
        "friday.infrastructure.brain",
        "friday.infrastructure.memory",
        "friday.infrastructure.tools",
    )
    offenders = [
        f"{path.relative_to(REPO_ROOT)}: imports {module}"
        for path in _python_files(COMPUTER_ROOT)
        for module in _imports(path)
        if module.startswith(forbidden_prefixes)
    ]

    assert offenders == []
