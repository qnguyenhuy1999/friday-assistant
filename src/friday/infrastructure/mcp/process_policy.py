"""The single safe argv validation policy for MCP server processes."""

from __future__ import annotations

from friday.infrastructure.mcp.errors import McpConfigInvalid

SHELL_INTERPRETERS = frozenset(
    {
        "sh",
        "bash",
        "zsh",
        "dash",
        "ksh",
        "fish",
        "csh",
        "tcsh",
        "ash",
        "busybox",
        "cmd",
        "cmd.exe",
        "command.com",
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
    }
)
_WRAPPERS = frozenset({"env", "command", "nohup", "setsid"})


def validate_server_command(command: tuple[str, ...]) -> None:
    if not command:
        raise McpConfigInvalid("command must be a non-empty argument list")
    if any(not part.strip() for part in command):
        raise McpConfigInvalid("command must not contain a blank argument")
    executable = command[0].replace("\\", "/").rsplit("/", maxsplit=1)[-1].strip().casefold()
    if executable in SHELL_INTERPRETERS:
        raise McpConfigInvalid("command must not use a shell interpreter as its executable")
    # A wrapper does not make a shell safe.  Supporting all wrapper grammars
    # would turn this policy into a shell parser, so reject known launchers
    # outright rather than accidentally accepting `env bash -c ...`.
    if executable in _WRAPPERS:
        raise McpConfigInvalid("command must not use an interpreter wrapper")
