"""Role capabilities and workflow contracts for the Hermes Devero fleet."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RolePolicy:
    profile: str
    title: str
    model: str
    reasoning_effort: str
    actions: tuple[str, ...]
    can_read_devero: bool
    can_write_pending: bool
    can_save_pending: bool


ROLE_POLICIES: dict[str, RolePolicy] = {
    "chief": RolePolicy(
        profile="devero",
        title="Chief of Staff",
        model="gpt-5.6-sol",
        reasoning_effort="high",
        actions=(
            "case_status",
            "authorize_save",
            "approve_corrections",
            "cancel_case",
            "close_case",
        ),
        can_read_devero=False,
        can_write_pending=False,
        can_save_pending=False,
    ),
    "writer": RolePolicy(
        profile="writer",
        title="Writer",
        model="gpt-5.6-sol",
        reasoning_effort="high",
        actions=(
            "resolve_case",
            "inspect_punch_list",
            "apply_corrections",
            "save_pending",
        ),
        can_read_devero=True,
        can_write_pending=True,
        can_save_pending=True,
    ),
    "verifier": RolePolicy(
        profile="verifier",
        title="Verifier",
        model="gpt-5.6-sol",
        reasoning_effort="high",
        actions=("resolve_case", "verify_case", "verify_persistence"),
        can_read_devero=True,
        can_write_pending=False,
        can_save_pending=False,
    ),
    "copier": RolePolicy(
        profile="copier",
        title="Copier",
        model="gpt-5.6-luna",
        reasoning_effort="medium",
        actions=("resolve_case", "copy_to_pending", "report_transfer"),
        can_read_devero=True,
        can_write_pending=True,
        can_save_pending=True,
    ),
}

PROFILE_TO_ROLE = {policy.profile: role for role, policy in ROLE_POLICIES.items()}


def normalize_role(value: str) -> str:
    role = value.strip().lower()
    role = PROFILE_TO_ROLE.get(role, role)
    if role not in ROLE_POLICIES:
        raise ValueError(f"Unknown Devero fleet role: {value!r}")
    return role


def allowed_actions(role: str) -> tuple[str, ...]:
    return ROLE_POLICIES[normalize_role(role)].actions
