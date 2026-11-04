"""HOST_<NAME> host profiles and an optional SSH_KEYS global private-key map."""

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

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
    if not isinstance(name, str) or not name.startswith("HOST_"):
        raise ConfigurationError("Host must be a HOST_<NAME> environment variable")
    raw = os.environ.get(name)
    if raw is None:
        raise ConfigurationError(f"Host {name} is not configured")
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
    if isinstance(host, str) and host.startswith("ssh://"):
        try:
            uri = urlsplit(host)
            hostname = uri.hostname
            username = unquote(uri.username) if uri.username is not None else None
            port = uri.port if uri.port is not None else 22
        except ValueError as exc:
            raise ConfigurationError("Invalid SSH URL") from exc
        if (not hostname or not username or "\x00" in username or "\x00" in hostname
                or uri.password is not None or uri.path not in ("", "/")
                or uri.query or uri.fragment or not 1 <= port <= 65535):
            raise ConfigurationError(
                "SSH URL must be ssh://username@host[:port] without password, path or query"
            )
        return Target(host, hostname, username, port)

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
    for name in sorted(os.environ):
        if not name.startswith("HOST_"):
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


def connection_options(target: Target) -> Iterator[dict]:
    """Build ordered authentication attempts from environment-provided credentials.

    Dynamic ssh:// hosts only receive SSH_KEYS. Configured hosts retain the
    host private key -> host password -> global private keys precedence.
    """
    profile = {} if target.name.startswith("ssh://") else _profile(target.name)
    password = profile.get("password")
    private_key = profile.get("private_key")
    if password is not None and (not isinstance(password, str) or not password):
        raise ConfigurationError(f"{target.name} password must be a nonempty string")

    host_key = (_import_key(private_key, target.name, profile.get("passphrase"))
                if private_key is not None else None)
    common = {
        "host": target.hostname, "username": target.username, "port": target.port,
        "known_hosts": _known_hosts(profile, target.name),
        "config": None, "agent_path": None, "client_keys": None,
        "client_certs": [], "gss_auth": False, "gss_kex": False,
        "host_based_auth": False,
    }
    if host_key is not None:
        yield {**common, "client_keys": [host_key],
               "preferred_auth": ["publickey"]}
    if password:
        yield {**common, "password": password,
               "preferred_auth": ["password", "keyboard-interactive"]}

    # Do not import global keys unless all host-specific attempts were rejected.
    global_keys = [_import_key(raw, f"SSH_KEYS[{name}]")
                   for name, raw in _global_keys().items()]
    if global_keys:
        yield {**common, "client_keys": global_keys,
               "preferred_auth": ["publickey"]}
    elif host_key is None and not password:
        raise ConfigurationError(f"{target.name} requires private_key, password or SSH_KEYS")


def get_public_keys(host: str | None = None) -> dict:
    """Return plaintext public keys, derived from stored private keys."""
    keys = [
        {"source": "global", "name": name,
         "public_key": _import_key(raw, f"SSH_KEYS[{name}]")
                       .export_public_key().decode("utf-8").strip()}
        for name, raw in _global_keys().items()
    ]
    if host is not None and not host.startswith("ssh://"):
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
