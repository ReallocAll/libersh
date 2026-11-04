"""SSH_<NAME> host profiles and an optional SSH_KEYS global private-key map."""

import json
import os
from dataclasses import dataclass

import asyncssh


class ConfigurationError(ValueError):
    """SSH configuration failure without exposing authentication material."""


@dataclass(frozen=True)
class Target:
    name: str
    hostname: str
    username: str
    port: int


def _profile(name: str) -> dict:
    if not isinstance(name, str) or not name.startswith("SSH_") or name == "SSH_KEYS":
        raise ConfigurationError("Host must be an SSH_<NAME> environment variable")
    raw = os.environ.get(name)
    if raw is None:
        raise ConfigurationError(f"SSH host {name} is not configured")
    try:
        config = json.loads(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must contain a JSON object") from exc
    if not isinstance(config, dict):
        raise ConfigurationError(f"{name} must contain a JSON object")
    return config


def _global_keys() -> dict:
    raw = os.environ.get("SSH_KEYS", "{}")
    try:
        keys = json.loads(raw)
    except ValueError as exc:
        raise ConfigurationError("SSH_KEYS must contain a JSON object") from exc
    if not isinstance(keys, dict):
        raise ConfigurationError("SSH_KEYS must contain a JSON object")
    return keys


def _import_key(raw: str, label: str, passphrase: str | None = None):
    if not isinstance(raw, str) or not raw:
        raise ConfigurationError(f"{label} must contain an OpenSSH private key")
    try:
        return asyncssh.import_private_key(raw, passphrase=passphrase)
    except (ValueError, asyncssh.KeyImportError) as exc:
        raise ConfigurationError(f"Invalid private key in {label}") from exc


def resolve_target(host: str) -> Target:
    config = _profile(host)
    hostname = config.get("host")
    username = config.get("username")
    port = config.get("port", 22)
    if not isinstance(hostname, str) or not hostname or "\x00" in hostname:
        raise ConfigurationError(f"{host} requires a nonempty host")
    if not isinstance(username, str) or not username or "\x00" in username:
        raise ConfigurationError(f"{host} requires a nonempty username")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ConfigurationError(f"{host} port must be between 1 and 65535")
    return Target(host, hostname, username, port)


def list_hosts() -> dict:
    """Expose host aliases and connection metadata, never credentials."""
    hosts = []
    for name, raw in sorted(os.environ.items()):
        if name == "SSH_KEYS" or not name.startswith("SSH_") or not raw.lstrip().startswith("{"):
            continue
        target = resolve_target(name)
        profile = _profile(name)
        item = {"name": target.name, "host": target.hostname,
                "username": target.username, "port": target.port}
        if isinstance(profile.get("desc"), str):
            item["desc"] = profile["desc"]
        hosts.append(item)
    return {"hosts": hosts}


def _known_hosts(profile: dict, name: str):
    raw = profile.get("known_hosts")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw:
        raise ConfigurationError(f"{name} known_hosts must be a nonempty string")
    try:
        return asyncssh.import_known_hosts(raw)
    except ValueError as exc:
        raise ConfigurationError(f"Invalid known_hosts in {name}") from exc


def connection_options(target: Target) -> list[dict]:
    """Build ordered, isolated authentication attempts from environment secrets.

    Password must run between host and global private keys, so these stages
    use separate SSH connections rather than AsyncSSH's publickey-first default.
    """
    profile = _profile(target.name)
    password = profile.get("password")
    private_key = profile.get("private_key")
    if password is not None and (not isinstance(password, str) or not password):
        raise ConfigurationError(f"{target.name} password must be a nonempty string")

    host_key = (_import_key(private_key, target.name, profile.get("passphrase"))
                if private_key is not None else None)
    global_keys = [_import_key(raw, f"SSH_KEYS[{name}]")
                   for name, raw in _global_keys().items()]

    common = {
        "host": target.hostname, "username": target.username, "port": target.port,
        "known_hosts": _known_hosts(profile, target.name),
        "config": None, "agent_path": None, "client_keys": None,
        "client_certs": [], "gss_auth": False, "gss_kex": False,
        "host_based_auth": False,
    }
    attempts = []
    if host_key is not None:
        attempts.append({**common, "client_keys": [host_key],
                         "preferred_auth": ["publickey"]})
    if password:
        attempts.append({**common, "password": password,
                         "preferred_auth": ["password", "keyboard-interactive"]})
    if global_keys:
        attempts.append({**common, "client_keys": global_keys,
                         "preferred_auth": ["publickey"]})
    if not attempts:
        raise ConfigurationError(f"{target.name} requires private_key, password or SSH_KEYS")
    return attempts


def get_public_keys(host: str | None = None) -> dict:
    """Return plaintext public keys, derived from stored private keys."""
    keys = [
        {"source": "global", "name": name,
         "public_key": _import_key(raw, f"SSH_KEYS[{name}]")
                       .export_public_key().decode("utf-8").strip()}
        for name, raw in _global_keys().items()
    ]
    if host is not None:
        profile = _profile(host)
        raw = profile.get("private_key")
        if raw is not None:
            keys.append({
                "source": "host", "name": host,
                "public_key": _import_key(raw, host, profile.get("passphrase"))
                              .export_public_key().decode("utf-8").strip(),
            })
    return {"keys": keys}


def job_base() -> str:
    base = os.environ.get("LIBERSH_JOB_BASE", "/var/tmp")
    if not base.startswith("/") or "\x00" in base:
        raise ConfigurationError("LIBERSH_JOB_BASE must be an absolute path")
    return base.rstrip("/") or "/"
