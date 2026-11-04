"""Single JSON SSH_<NAME> profile configuration contracts."""
import json

import asyncssh
import pytest

from libersh.config import (
    ConfigurationError, connect_kwargs, list_hosts, resolve_target,
)


def test_multiple_hosts_and_secret_free_discovery(monkeypatch):
    monkeypatch.setenv("SSH_PROD", json.dumps({
        "host": "192.0.2.10", "username": "ubuntu", "port": 2222,
        "password": "prod-secret", "desc": "Production server",
    }))
    monkeypatch.setenv("SSH_DEV", json.dumps({
        "host": "dev.example.org", "username": "root", "password": "dev-secret",
    }))
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/ssh-agent.sock")
    target = resolve_target("SSH_PROD")
    assert (target.hostname, target.username, target.port) == (
        "192.0.2.10", "ubuntu", 2222)
    assert connect_kwargs(target)["password"] == "prod-secret"
    assert connect_kwargs(target)["known_hosts"] is None
    hosts = list_hosts()
    assert {x["name"] for x in hosts["hosts"]} == {"SSH_DEV", "SSH_PROD"}
    assert next(x for x in hosts["hosts"] if x["name"] == "SSH_PROD")["desc"] == "Production server"
    assert "prod-secret" not in repr(hosts)
    assert "dev-secret" not in repr(hosts)


def test_private_key(monkeypatch):
    key = asyncssh.generate_private_key("ssh-ed25519")
    monkeypatch.setenv("SSH_KEYED", json.dumps({
        "host": "example.org", "username": "ubuntu",
        "private_key": key.export_private_key().decode("utf-8"),
    }))
    target = resolve_target("SSH_KEYED")
    options = connect_kwargs(target)
    assert len(options["client_keys"]) == 1
    assert options["known_hosts"] is None
    assert "private_key" not in repr(list_hosts())


def test_verified_known_hosts_optional(monkeypatch):
    key = asyncssh.generate_private_key("ssh-ed25519")
    public = key.export_public_key().decode().strip()
    monkeypatch.setenv("SSH_VERIFIED", json.dumps({
        "host": "example.org", "username": "tester", "password": "secret",
        "known_hosts": "example.org " + public,
    }))
    assert connect_kwargs(resolve_target("SSH_VERIFIED"))["known_hosts"] is not None


@pytest.mark.parametrize("value,match", [
    ('not-json', "JSON object"),
    ('[]', "JSON object"),
    ('{"host":"example.org","username":"root","port":true}', "port"),
    ('{"host":"example.org","username":"root","port":65536}', "port"),
    ('{"host":"","username":"root","password":"x"}', "host"),
    ('{"host":"example.org","username":""}', "username"),
])
def test_invalid_configuration(monkeypatch, value, match):
    monkeypatch.setenv("SSH_BAD", value)
    with pytest.raises(ConfigurationError, match=match):
        resolve_target("SSH_BAD")


def test_missing_or_invalid_credentials(monkeypatch):
    monkeypatch.setenv("SSH_EMPTY", json.dumps({
        "host": "example.org", "username": "root",
    }))
    with pytest.raises(ConfigurationError, match="password or private_key"):
        connect_kwargs(resolve_target("SSH_EMPTY"))
    monkeypatch.setenv("SSH_EMPTY", json.dumps({
        "host": "example.org", "username": "root", "private_key": "not-a-key",
    }))
    with pytest.raises(ConfigurationError, match="Invalid private_key"):
        connect_kwargs(resolve_target("SSH_EMPTY"))


def test_no_dynamic_host_fallback(monkeypatch):
    monkeypatch.setenv("SSH_PROD", json.dumps({
        "host": "example.org", "username": "ubuntu", "password": "secret",
    }))
    with pytest.raises(ConfigurationError, match="environment variable"):
        resolve_target("example.org")
    with pytest.raises(ConfigurationError, match="not configured"):
        resolve_target("SSH_UNKNOWN")
