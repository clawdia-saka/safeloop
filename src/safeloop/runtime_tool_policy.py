"""Policy profiles for SafeLoop runtime tool routing and execution."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Sequence

Route = Literal["allow_read_only", "quarantine", "external_outbox", "manual_review"]
PolicyProfile = Literal["strict-local", "agent-dev", "ci-readonly"]

DEFAULT_POLICY_PROFILE: PolicyProfile = "strict-local"
POLICY_PROFILES: tuple[PolicyProfile, ...] = ("strict-local", "agent-dev", "ci-readonly")

_READ_ONLY_EXECUTABLES = {
    "cat",
    "diff",
    "grep",
    "head",
    "ls",
    "pwd",
    "rg",
    "stat",
    "tail",
    "wc",
}
_READ_ONLY_TERMS = {
    *_READ_ONLY_EXECUTABLES,
    "find",
    "inspect",
    "less",
    "list",
    "open",
    "read",
    "search",
    "show",
    "view",
}
_READ_ONLY_TOOL_ACTIONS = {
    "git": {"diff", "log", "show", "status"},
}
_AGENT_DEV_SAFE_ACTIONS = {
    "python": {"help", "version"},
    "sh": {"syntax_check"},
}
_GIT_LOCAL_MUTATION_ACTIONS = {
    "add",
    "apply",
    "checkout",
    "clean",
    "commit",
    "merge",
    "mv",
    "rebase",
    "reset",
    "restore",
    "rm",
    "switch",
}
_TOOL_ALIASES = {
    "python3": "python",
    "bash": "sh",
    "zsh": "sh",
}
_EXECUTABLE_ALIASES = {
    "python": {"python", "python3"},
    "sh": {"sh", "bash", "zsh"},
}
_GIT_OPTION_VALUE_FLAGS = {"-C", "-c", "--git-dir", "--work-tree"}


class RuntimeToolPolicyError(ValueError):
    """Raised when a policy profile cannot be resolved."""


def normalize_policy_profile(profile: str | None) -> PolicyProfile:
    value = (profile or DEFAULT_POLICY_PROFILE).strip() or DEFAULT_POLICY_PROFILE
    if value not in POLICY_PROFILES:
        raise RuntimeToolPolicyError(f"policy_profile must be one of: {', '.join(POLICY_PROFILES)}")
    return value  # type: ignore[return-value]


def policy_profile_choices() -> tuple[str, ...]:
    return POLICY_PROFILES


def _tokens(*values: str) -> set[str]:
    raw = " ".join(values).lower()
    return {part for part in re.split(r"[^a-z0-9_+-]+", raw) if part}


def _canonical_tool(tool: str) -> str:
    value = tool.strip().lower()
    return _TOOL_ALIASES.get(value, value)


def _tool_action(tool: str, action: str) -> tuple[str, str]:
    return _canonical_tool(tool), action.strip().lower().replace("-", "_")


def is_read_only_request(tool: str, action: str) -> bool:
    tool_value, action_value = _tool_action(tool, action)
    if tool_value in _READ_ONLY_EXECUTABLES:
        return True
    if action_value in _READ_ONLY_TOOL_ACTIONS.get(tool_value, set()):
        return True
    terms = _tokens(tool_value, action_value)
    return bool(terms) and terms <= _READ_ONLY_TERMS


def is_agent_dev_safe_request(tool: str, action: str) -> bool:
    tool_value, action_value = _tool_action(tool, action)
    return action_value in _AGENT_DEV_SAFE_ACTIONS.get(tool_value, set())


def resolve_policy_route(
    *,
    policy_profile: str | None,
    tool: str,
    action: str,
    default_route: Route,
    default_reason: str,
) -> tuple[Route, str, PolicyProfile]:
    """Apply a policy profile to the default firewall route."""

    profile = normalize_policy_profile(policy_profile)
    tool_value, action_value = _tool_action(tool, action)
    if is_read_only_request(tool_value, action_value):
        return "allow_read_only", f"{profile} policy allows read-only tool/action", profile
    if profile == "agent-dev" and is_agent_dev_safe_request(tool_value, action_value):
        return "allow_read_only", "agent-dev policy allows safe local dev tool/action", profile
    if profile == "ci-readonly":
        return "manual_review", "ci-readonly policy requires manual review for non-read-only request", profile
    if tool_value == "git" and action_value in _GIT_LOCAL_MUTATION_ACTIONS:
        return "quarantine", f"{profile} policy treats git {action_value} as local mutation", profile
    return default_route, default_reason, profile


def _command_action(tool: str, action: str, command: Sequence[str]) -> str:
    tool_value, action_value = _tool_action(tool, action)
    if tool_value == "git" and len(command) >= 2:
        skip_next = False
        for arg in command[1:]:
            if skip_next:
                skip_next = False
                continue
            if arg in _GIT_OPTION_VALUE_FLAGS:
                skip_next = True
                continue
            if arg == "--" or arg.startswith("-"):
                continue
            return Path(arg).name.lower().replace("-", "_")
        return action_value
    if tool_value == "python" and len(command) >= 2 and command[1] in {"--version", "-V"}:
        return "version"
    if tool_value == "python" and len(command) >= 2 and command[1] in {"--help", "-h"}:
        return "help"
    if tool_value == "sh" and len(command) >= 2 and command[1] == "-n":
        return "syntax_check"
    return action_value


def execution_policy_block_reason(
    *,
    policy_profile: str | None,
    tool: str,
    action: str,
    command: Sequence[str],
) -> str | None:
    """Return why an argv command is not executable under the policy profile."""

    if not command:
        return "command is required"
    profile = normalize_policy_profile(policy_profile)
    executable = Path(command[0]).name
    tool_value = tool.strip().lower()
    canonical_tool = _canonical_tool(tool_value)
    allowed_executables = _EXECUTABLE_ALIASES.get(canonical_tool, {tool_value})
    if executable not in allowed_executables:
        return f"command executable {executable!r} does not match tool {tool_value!r}"
    command_action = _command_action(tool_value, action, command)
    if is_read_only_request(canonical_tool, command_action):
        return None
    if profile == "agent-dev" and is_agent_dev_safe_request(canonical_tool, command_action):
        return None
    return f"tool {tool_value!r} action {command_action!r} is not executable under policy profile {profile!r}"
