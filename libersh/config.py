"""One SSH_<NAME> JSON environment variable per remote host."""

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
    if not isinstance(name, str) or not name.startswith("SSH_"):
        raise ConfigurationError("Host must be an SSH_* environment variable name")
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
    """Expose host aliases and connection metadata, never their credentials."""
    hosts = []
    for name, raw in sorted(os.environ.items()):
        if not name.startswith("SSH_") or not raw.lstrip().startswith("{"):
            continue
        target = resolve_target(name)
        profile = _profile(name)
        item = {"name": target.name, "host": target.hostname,
                "username": target.username, "port": target.port}
        if isinstance(profile.get("desc"), str):
            item["desc"] = profile["desc"]
        hosts.append(item)
    return {"hosts": hosts}


def connect_kwargs(target: Target) -> dict:
    profile = _profile(target.name)
    password = profile.get("password")
    private_key = profile.get("private_key")
    if not ((isinstance(password, str) and password) or
            (isinstance(private_key, str) and private_key)):
        raise ConfigurationError(f"{target.name} requires password or private_key")

    kwargs = {"host": target.hostname, "port": target.port,
              "username": target.username}
    if isinstance(password, str) and password:
        kwargs["password"] = password
    if isinstance(private_key, str) and private_key:
        try:
            kwargs["client_keys"] = [asyncssh.import_private_key(
                private_key, passphrase=profile.get("passphrase"))]
        except (ValueError, asyncssh.KeyImportError) as exc:
            raise ConfigurationError(f"Invalid private_key in {target.name}") from exc

    # No trust material is required for quick setup. Supplying known_hosts
    # enables verified server identity; omission explicitly opts out.
    known_hosts = profile.get("known_hosts")
    if known_hosts is None:
        kwargs["known_hosts"] = None
    elif isinstance(known_hosts, str) and known_hosts:
        try:
            kwargs["known_hosts"] = asyncssh.import_known_hosts(known_hosts)
        except ValueError as exc:
            raise ConfigurationError(f"Invalid known_hosts in {target.name}") from exc
    else:
        raise ConfigurationError(f"{target.name} known_hosts must be a nonempty string")
    return kwargs


def job_base() -> str:
    base = os.environ.get("LIBERSH_JOB_BASE", "/var/tmp")
    if not base.startswith("/") or "\x00" in base:
        raise ConfigurationError("LIBERSH_JOB_BASE must be an absolute path")
    return base.rstrip("/") or "/"
