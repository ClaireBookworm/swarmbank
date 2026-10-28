"""Hard guards: official CLIs only, direct provider connections, no proxy.

Every launch and every account check goes through these functions. A failed
guard blocks the account; it is never bypassed automatically.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import config

# Anything that could redirect a CLI to another endpoint or credential.
FORBIDDEN_ENV = re.compile(
    r"(BASE_URL|_PROXY$|^PROXY|API_KEY|AUTH_TOKEN|OAUTH_TOKEN|ACCESS_TOKEN|^ANTHROPIC_|^OPENAI_|^CODEX_|^CLAUDE_)",
    re.IGNORECASE,
)
# Text that must never appear in a CLI profile's configuration.
FORBIDDEN_TEXT = re.compile(r"8317|proxy-sci|cli-proxy|BASE_URL|base_url|apiKeyHelper|model_providers", re.IGNORECASE)


class GuardError(RuntimeError):
    pass


def clean_env(extra: dict | None = None, home: str | None = None) -> dict:
    """Build an environment from scratch. Nothing provider-related is inherited."""
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": home or str(Path.home()),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "TERM": os.environ.get("TERM", "xterm-256color"),
        "DISABLE_AUTOUPDATER": "1",
    }
    if extra:
        env.update(extra)
    for key in env:
        if FORBIDDEN_ENV.search(key) and key not in ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_TMPDIR", "CODEX_HOME"):
            raise GuardError(f"refusing to pass environment variable {key}")
    return env


def claude_binary() -> Path:
    found = shutil.which("claude")
    if not found:
        raise GuardError("official Claude Code CLI (claude) is not installed")
    return Path(found).resolve()


def codex_binary() -> Path:
    found = shutil.which("codex")
    if not found:
        raise GuardError("official Codex CLI (codex) is not installed")
    if sys.platform == "darwin":  # official native build: Homebrew cask binary or the npm darwin-arm64 package
        resolved = Path(found).resolve()
        if resolved.name == "codex" and resolved.parent.name == "bin" and "Caskroom" in resolved.parts:
            return resolved
        vendor = sorted(resolved.parent.parent.glob(
            "node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex"))
        if not vendor:
            raise GuardError("cannot locate the native macOS Codex binary")
        return vendor[0]
    vendor = sorted(Path(found).resolve().parent.parent.glob(
        "node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex"))
    if not vendor:
        raise GuardError("cannot locate the native Codex binary inside the official npm package")
    return vendor[0]


def binary(provider: str) -> Path:
    return claude_binary() if provider == "claude" else codex_binary()


def profile_env(account: dict) -> dict:
    home = config.account_home(account["id"])
    key = "CLAUDE_CONFIG_DIR" if account["provider"] == "claude" else "CODEX_HOME"
    return {key: str(home)}


def _scan_text(path: Path) -> None:
    if path.is_file():
        text = path.read_text("utf-8", errors="replace")
        match = FORBIDDEN_TEXT.search(text)
        if match:
            raise GuardError(f"{path.name} contains forbidden setting {match.group(0)!r}")


def check_profile_files(account: dict) -> None:
    home = config.account_home(account["id"])
    if account["provider"] == "claude":
        for name in ("settings.json", "settings.local.json"):
            _scan_text(home / name)
        state = home / ".claude.json"
        if state.is_file():
            data = json.loads(state.read_text("utf-8") or "{}")
            if data.get("primaryApiKey"):
                raise GuardError(".claude.json stores an API key; subscription login only")
            env = (data.get("env") or {})
            if any(FORBIDDEN_ENV.search(k) for k in env):
                raise GuardError(".claude.json sets provider environment overrides")
    else:
        _scan_text(home / "config.toml")
        auth = home / "auth.json"
        if auth.is_file():
            data = json.loads(auth.read_text("utf-8"))
            if data.get("OPENAI_API_KEY"):
                raise GuardError("auth.json contains an API key; ChatGPT subscription login only")
            if data.get("auth_mode") not in (None, "chatgpt"):
                raise GuardError(f"auth.json auth_mode is {data.get('auth_mode')!r}, expected 'chatgpt'")


def _find_email(value) -> str | None:
    if isinstance(value, dict):
        for k, v in value.items():
            if k.lower() in ("email", "emailaddress", "email_address") and isinstance(v, str):
                return v
            found = _find_email(v)
            if found:
                return found
    if isinstance(value, list):
        for v in value:
            found = _find_email(v)
            if found:
                return found
    return None


def identity(account: dict) -> dict:
    """Read the logged-in identity from the official CLI. Never returns tokens."""
    check_profile_files(account)
    env = clean_env(profile_env(account))
    if account["provider"] == "claude":
        cmd, cwd = [str(claude_binary()), "auth", "status", "--json"], None
        if sys.platform == "darwin":  # the sandbox hides the host home, so start inside the profile
            from . import macos
            cwd = config.account_home(account["id"])
            env["HOME"] = str(cwd)
            cmd = macos.wrap(cmd, macos.account_profile(cwd, macos.cli_root("claude", claude_binary())))
        out = subprocess.run(cmd, env=env, cwd=cwd,
                             capture_output=True, text=True, timeout=60)
        try:
            data = json.loads(out.stdout)
        except ValueError:
            return {"logged_in": False, "error": (out.stderr or out.stdout).strip()[:300]}
        method = str(data.get("authMethod") or "")
        email = _find_email(data)
        state = config.account_home(account["id"]) / ".claude.json"
        if not email and state.is_file():
            email = _find_email((json.loads(state.read_text("utf-8") or "{}")).get("oauthAccount") or {})
        result = {
            "logged_in": bool(data.get("loggedIn")),
            "email": email,
            "auth_method": method,
            "api_provider": data.get("apiProvider"),
            "plan": data.get("subscriptionType") or data.get("subscription") or data.get("plan"),
        }
        if result["logged_in"]:
            if data.get("apiProvider") != "firstParty":
                raise GuardError(f"Claude apiProvider is {data.get('apiProvider')!r}; expected firstParty")
            if re.search(r"api.?key|console", method, re.IGNORECASE):
                raise GuardError(f"Claude auth method {method!r} is not a subscription login")
        return result
    auth = config.account_home(account["id"]) / "auth.json"
    if not auth.is_file():
        return {"logged_in": False}
    data = json.loads(auth.read_text("utf-8"))
    token = (data.get("tokens") or {}).get("id_token")
    claims = {}
    if token:
        claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
    openai = claims.get("https://api.openai.com/auth", {})
    return {
        "logged_in": data.get("auth_mode") == "chatgpt" and bool(token),
        "email": claims.get("email"),
        "auth_method": data.get("auth_mode"),
        "plan": openai.get("chatgpt_plan_type"),
    }


def verify(cfg: dict, account: dict) -> dict:
    """Identity must match the configured, non-excluded email."""
    info = identity(account)
    if not info.get("logged_in"):
        info["ok"] = False
        info["problem"] = "not logged in"
        return info
    email = (info.get("email") or "").lower()
    excluded = [e.lower() for e in cfg["excluded_emails"].get(account["provider"], [])]
    if email in excluded:
        raise GuardError(f"{account['id']} is logged into excluded account {email}")
    if email != account["email"].lower():
        raise GuardError(f"{account['id']} is logged into {email or 'an unknown account'}, expected {account['email']}")
    info["ok"] = True
    return info
