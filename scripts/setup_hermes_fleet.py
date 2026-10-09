#!/usr/bin/env python3
"""Validate and install the native Hermes Bot Mode Devero fleet."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
PACKAGE = WORKSPACE / "hermes"
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))
HERMES_HOME = Path(os.environ.get("HERMES_HOME_ROOT", Path.home() / ".hermes"))
PROFILES_ROOT = HERMES_HOME / "profiles"
ROLES = {
    "chief": {
        "profile": "devero", "model": "gpt-5.6-sol", "provider": "openai-codex",
        "reasoning": "high", "chrome": False,
    },
    "writer": {
        "profile": "writer", "model": "gpt-5.6-sol", "provider": "openai-codex",
        "reasoning": "high", "chrome": True,
    },
    "verifier": {
        "profile": "verifier", "model": "gpt-5.6-sol", "provider": "openai-codex",
        "reasoning": "high", "chrome": True,
    },
    "copier": {
        "profile": "copier", "model": "gpt-5.6-luna", "provider": "openai-codex",
        "reasoning": "medium", "chrome": True,
    },
}
REQUIRED_SKILLS = {"devero-fleet", "devero-chief", "devero-writer", "devero-verifier", "devero-copier"}
CHANNEL_SECRET_PREFIXES = ("TELEGRAM_", "DISCORD_", "SLACK_", "WHATSAPP_")


class SetupError(RuntimeError):
    pass


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if check and result.returncode:
        raise SetupError(f"Command failed ({' '.join(args[:4])}): {result.stdout.strip()}")
    return result


def _profile_dir(profile: str) -> Path:
    return PROFILES_ROOT / profile


def _assert_private(path: Path) -> None:
    if not path.exists():
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if path.is_dir() and mode & 0o077:
        os.chmod(path, 0o700)
    elif path.is_file() and mode & 0o077:
        os.chmod(path, 0o600)


def verify_package() -> None:
    missing: list[str] = []
    for role in ROLES:
        for name in ("SOUL.md", "profile.yaml", "config-overlay.yaml"):
            path = PACKAGE / "profiles" / role / name
            if not path.is_file():
                missing.append(str(path.relative_to(WORKSPACE)))
    for skill in REQUIRED_SKILLS:
        path = PACKAGE / "skills" / skill / "SKILL.md"
        if not path.is_file():
            missing.append(str(path.relative_to(WORKSPACE)))
        elif "TODO" in path.read_text(encoding="utf-8"):
            raise SetupError(f"Unfinished skill: {skill}")
    if missing:
        raise SetupError("Missing package files: " + ", ".join(missing))

    from devero_fleet.policy import ROLE_POLICIES

    for role, expected in ROLES.items():
        policy = ROLE_POLICIES[role]
        if (policy.profile, policy.model, policy.reasoning_effort) != (
            expected["profile"], expected["model"], expected["reasoning"]
        ):
            raise SetupError(f"Policy/template mismatch for {role}")
        overlay = (PACKAGE / "profiles" / role / "config-overlay.yaml").read_text(encoding="utf-8")
        for fragment in (expected["model"], expected["provider"], expected["reasoning"], "bot_mode_protocol: true"):
            if fragment not in overlay:
                raise SetupError(f"Missing {fragment!r} in {role} overlay")
    if any("sign" in action.lower() for policy in ROLE_POLICIES.values() for action in policy.actions):
        raise SetupError("A role exposes a signing action")
    print("HERMES FLEET PACKAGE VERIFIED")


def privacy_check() -> None:
    roots = [WORKSPACE / "devero_fleet", PACKAGE, Path(__file__)]
    text_files: list[Path] = []
    for root in roots:
        if root.is_file():
            text_files.append(root)
        else:
            text_files.extend(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    forbidden_patterns = {
        "concrete case id": re.compile(r"\bDV-[A-F0-9]{12}\b"),
        "US phone number": re.compile(r"\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b"),
        "concrete slash date": re.compile(r"\b(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01])/(?:19|20)\d{2}\b"),
    }
    for path in text_files:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in forbidden_patterns.items():
            if pattern.search(content):
                raise SetupError(f"Persistent artifact contains a {label}: {path.relative_to(WORKSPACE)}")
    if (WORKSPACE / "devero_fleet" / "operations.py").exists():
        raise SetupError("Custom Devero MCP operations layer must not be present")
    print("HERMES FLEET PRIVACY VERIFIED")


def _backup_chief() -> Path:
    chief = _profile_dir("devero")
    marker = chief / ".devero-fleet-installed.json"
    if marker.is_file():
        data = json.loads(marker.read_text(encoding="utf-8"))
        return Path(data["backup"])
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = HERMES_HOME / "backups" / "devero-fleet" / stamp
    backup.mkdir(parents=True, mode=0o700)
    for name in ("config.yaml", ".env", "SOUL.md", "profile.yaml"):
        source = chief / name
        if source.exists():
            shutil.copy2(source, backup / name)
    legacy = chief / "skills" / "devero"
    if legacy.exists():
        shutil.copytree(legacy, backup / "legacy-devero-skill", dirs_exist_ok=True)
    _assert_private(backup)
    return backup


def _strip_worker_channels(profile_dir: Path) -> None:
    env_path = profile_dir / ".env"
    if not env_path.exists():
        return
    kept = []
    for line in env_path.read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0].strip()
        if not key.startswith(CHANNEL_SECRET_PREFIXES):
            kept.append(line)
    env_path.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")
    os.chmod(env_path, 0o600)


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _set_env_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    replacement = f"{key}={value}"
    output: list[str] = []
    replaced = False
    for line in lines:
        if line.split("=", 1)[0].strip() == key:
            if not replaced:
                output.append(replacement)
                replaced = True
        else:
            output.append(line)
    if not replaced:
        output.append(replacement)
    fd, temporary = tempfile.mkstemp(prefix=".env-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, ("\n".join(output).rstrip() + "\n").encode("utf-8"))
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _ensure_chief_telegram_approvers(chief: Path) -> None:
    """Seed Chief's profile-scoped approvers without authorizing bot IDs."""
    chief_env_path = chief / ".env"
    chief_env = _read_env(chief_env_path)
    default_env = _read_env(HERMES_HOME / ".env")
    configured = chief_env.get("TELEGRAM_ALLOWED_USERS", "") or default_env.get(
        "TELEGRAM_ALLOWED_USERS", ""
    )
    candidates = [item.strip() for item in configured.split(",") if item.strip()]
    bot_ids: set[str] = set()
    for env in (chief_env, default_env):
        token = env.get("TELEGRAM_BOT_TOKEN", "")
        prefix, separator, _rest = token.partition(":")
        if separator and prefix.isdigit():
            bot_ids.add(prefix)
    human_ids = list(dict.fromkeys(item for item in candidates if item not in bot_ids))
    if not human_ids:
        raise SetupError(
            "Chief needs at least one human TELEGRAM_ALLOWED_USERS id for approval buttons"
        )
    _set_env_value(chief_env_path, "TELEGRAM_ALLOWED_USERS", ",".join(human_ids))


def _write_marker(profile_dir: Path, role: str, backup: Path) -> None:
    marker = profile_dir / ".devero-fleet-installed.json"
    marker.write_text(
        json.dumps({"role": role, "managed_by": str(Path(__file__).relative_to(WORKSPACE)), "backup": str(backup)}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(marker, 0o600)


def _copy_role_assets(role: str, profile_dir: Path, backup: Path) -> None:
    template = PACKAGE / "profiles" / role
    shutil.copy2(template / "SOUL.md", profile_dir / "SOUL.md")
    shutil.copy2(template / "profile.yaml", profile_dir / "profile.yaml")
    legacy = profile_dir / "skills" / "devero"
    if legacy.exists():
        if role == "chief" and not (backup / "legacy-devero-skill").exists():
            shutil.copytree(legacy, backup / "legacy-devero-skill", dirs_exist_ok=True)
        shutil.rmtree(legacy)
    for skill in ("devero-fleet", f"devero-{role}"):
        source = PACKAGE / "skills" / skill
        target = profile_dir / "skills" / skill
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)


def _configure_profile(role: str, profile: str, expected: dict) -> None:
    settings = {
        "model.default": expected["model"],
        "model.provider": expected.get("provider", "openai-codex"),
        "agent.reasoning_effort": expected["reasoning"],
        "agent.bot_mode_protocol": "true",
        "terminal.cwd": str(WORKSPACE),
        "kanban.orchestrator_profile": "devero",
        "memory.memory_enabled": "false",
        "memory.user_profile_enabled": "false",
        "security.redact_secrets": "true",
        "approvals.timeout": "300" if role == "chief" else "60",
        "mcp_servers.chrome-devtools-win.enabled": "true" if expected["chrome"] else "false",
        "toolsets": '["hermes-cli", "browser"]' if expected["chrome"] else '["hermes-cli"]',
    }
    for key, value in settings.items():
        _run("hermes", "-p", profile, "config", "set", "--force", key, value)
    if expected.get("base_url"):
        _run("hermes", "-p", profile, "config", "set", "--force", "model.base_url", expected["base_url"])
    else:
        _run("hermes", "-p", profile, "config", "unset", "model.base_url", check=False)
        _run("hermes", "-p", profile, "config", "unset", "model.api_key", check=False)


def _sync_custom_model_key(role: str, profile: str, expected: dict) -> None:
    """Propagate the Chief's custom-provider api_key into worker profiles without printing it."""
    if expected.get("provider") != "custom":
        return
    chief_text = (_profile_dir("devero") / "config.yaml").read_text(encoding="utf-8")
    block = re.search(r"(?ms)^model:\n(?:[ \t]+.*\n|\n)*?(?=^\S|\Z)", chief_text)
    key = None
    if block:
        key_match = re.search(r"(?m)^[ \t]+api_key:[ \t]*(\S+)[ \t]*$", block.group(0))
        if key_match:
            key = key_match.group(1)
    if not key:
        print(f"WARN: no chief model.api_key found; skip key sync for {profile}")
        return
    target = _profile_dir(profile) / "config.yaml"
    text = target.read_text(encoding="utf-8")
    model_block = re.search(r"(?ms)^model:\n(?:[ \t]+.*\n|\n)*?(?=^\S|\Z)", text)
    if not model_block:
        print(f"WARN: no model block in {profile} config; skip key sync")
        return
    block_text = model_block.group(0)
    if re.search(r"(?m)^[ \t]+api_key:[ \t]*\S+", block_text):
        new_block = re.sub(
            r"(?m)^([ \t]+api_key:)[ \t]*\S+[ \t]*$",
            lambda m: m.group(1) + " " + key, block_text, count=1,
        )
    else:
        new_block = re.sub(
            r"(?m)^([ \t]+base_url:[ \t]*\S+[ \t]*)$",
            lambda m: m.group(1) + "\n  api_key: " + key, block_text, count=1,
        )
    updated = text[: model_block.start()] + new_block + text[model_block.end() :]
    target.write_text(updated, encoding="utf-8")
    target.chmod(0o600)


def install() -> None:
    verify_package()
    chief = _profile_dir("devero")
    if not chief.is_dir():
        raise SetupError("Existing Hermes profile 'devero' is required as the Chief/Telegram profile")
    backup = _backup_chief()
    _ensure_chief_telegram_approvers(chief)

    # Create specialists before restricting Chief's browser surface. Cloning
    # retains the existing Windows Chrome DevTools command and model auth.
    for role in ("writer", "verifier", "copier"):
        profile = ROLES[role]["profile"]
        target = _profile_dir(profile)
        if not target.exists():
            _run("hermes", "profile", "create", profile, "--clone-from", "devero", "--no-alias")
        elif not (target / ".devero-fleet-installed.json").exists():
            raise SetupError(f"Refusing to overwrite unrelated existing Hermes profile: {profile}")
        _strip_worker_channels(target)

    for role, expected in ROLES.items():
        profile = expected["profile"]
        target = _profile_dir(profile)
        _copy_role_assets(role, target, backup)
        _configure_profile(role, profile, expected)
        _sync_custom_model_key(role, profile, expected)
        _write_marker(target, role, backup)

    boards = json.loads(_run("hermes", "kanban", "boards", "list", "--json").stdout)
    if not any(board.get("slug") == "devero" and not board.get("archived") for board in boards):
        _run(
            "hermes", "kanban", "boards", "create", "devero",
            "--name", "Devero",
            "--description", "PHI-minimized durable case routing for the Devero specialist fleet",
            "--default-workdir", str(WORKSPACE),
        )
    print(f"HERMES FLEET INSTALLED (backup: {backup})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("verify-package", "privacy-check", "install"))
    args = parser.parse_args(argv)
    try:
        if args.command == "verify-package":
            verify_package()
        elif args.command == "privacy-check":
            privacy_check()
        else:
            install()
        return 0
    except (SetupError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
