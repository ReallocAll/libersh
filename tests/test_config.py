"""Single JSON SSH_<NAME> profile configuration contracts."""
import json

import asyncssh
import pytest

from libersh.config import (
    ConfigurationError, connection_options, get_public_keys, list_hosts, resolve_target,
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
    assert list(connection_options(target))[0]["password"] == "prod-secret"
    assert list(connection_options(target))[0]["known_hosts"] is None
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
    options = list(connection_options(target))[0]
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
    assert list(connection_options(resolve_target("SSH_VERIFIED")))[0]["known_hosts"] is not None


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
    with pytest.raises(ConfigurationError, match="private_key, password or SSH_KEYS"):
        list(connection_options(resolve_target("SSH_EMPTY")))
    monkeypatch.setenv("SSH_EMPTY", json.dumps({
        "host": "example.org", "username": "root", "private_key": "not-a-key",
    }))
    with pytest.raises(ConfigurationError, match="Invalid private key"):
        list(connection_options(resolve_target("SSH_EMPTY")))


def test_no_dynamic_host_fallback(monkeypatch):
    monkeypatch.setenv("SSH_PROD", json.dumps({
        "host": "example.org", "username": "ubuntu", "password": "secret",
    }))
    with pytest.raises(ConfigurationError, match="environment variable"):
        resolve_target("example.org")
    with pytest.raises(ConfigurationError, match="not configured"):
        resolve_target("SSH_UNKNOWN")


def test_global_keys_only_and_public_key_export(monkeypatch):
    first = asyncssh.generate_private_key("ssh-ed25519")
    second = asyncssh.generate_private_key("ssh-ed25519")
    monkeypatch.setenv("SSH_KEYS", json.dumps({
        "first": first.export_private_key().decode(),
        "second": second.export_private_key().decode(),
    }))
    monkeypatch.setenv("SSH_BARE", json.dumps({
        "host": "example.org", "username": "ubuntu",
    }))
    opts = list(connection_options(resolve_target("SSH_BARE")))
    assert len(opts) == 1
    assert opts[0]["client_keys"][0].export_public_key() == first.export_public_key()
    assert opts[0]["client_keys"][1].export_public_key() == second.export_public_key()
    assert opts[0]["agent_path"] is None and opts[0]["config"] is None
    assert {x["name"] for x in list_hosts()["hosts"]} >= {"SSH_BARE"}
    assert all(x["name"] != "SSH_KEYS" for x in list_hosts()["hosts"])
    public = get_public_keys()
    assert [x["name"] for x in public["keys"]] == ["first", "second"]
    assert public["keys"][0]["public_key"] == first.export_public_key().decode().strip()
    assert "PRIVATE KEY" not in repr(public)


def test_ordered_host_private_key_password_then_global(monkeypatch):
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    global_key = asyncssh.generate_private_key("ssh-ed25519")
    monkeypatch.setenv("SSH_KEYS", json.dumps({"main": global_key.export_private_key().decode()}))
    monkeypatch.setenv("SSH_ORDER", json.dumps({
        "host": "example.org", "username": "root",
        "private_key": host_key.export_private_key().decode(),
        "password": "host-password",
    }))
    opts = list(connection_options(resolve_target("SSH_ORDER")))
    assert len(opts) == 3
    assert opts[0]["client_keys"][0].export_public_key() == host_key.export_public_key()
    assert "password" not in opts[0]
    assert opts[1]["client_keys"] is None
    assert opts[1]["password"] == "host-password"
    assert "password" not in opts[2]
    assert opts[2]["client_keys"][0].export_public_key() == global_key.export_public_key()
    assert all(x["agent_path"] is None and x["config"] is None for x in opts)
    assert all(x["client_certs"] == [] for x in opts)
    public = get_public_keys("SSH_ORDER")["keys"]
    assert [x["name"] for x in public] == ["main", "SSH_ORDER"]
    assert public[-1]["public_key"] == host_key.export_public_key().decode().strip()
    assert "host-password" not in repr(public)


def test_invalid_global_key_configuration(monkeypatch):
    monkeypatch.setenv("SSH_BARE", json.dumps({
        "host": "example.org", "username": "root",
    }))
    monkeypatch.setenv("SSH_KEYS", "not-json")
    with pytest.raises(ConfigurationError, match="SSH_KEYS must"):
        list(connection_options(resolve_target("SSH_BARE")))
    monkeypatch.setenv("SSH_KEYS", json.dumps({"broken": "not-a-private-key"}))
    with pytest.raises(ConfigurationError, match="Invalid private key"):
        list(connection_options(resolve_target("SSH_BARE")))


def test_global_key_loading_is_lazy(monkeypatch):
    monkeypatch.setenv("SSH_KEYS", "invalid-global-keys")
    monkeypatch.setenv("SSH_LOCAL_PASSWORD", json.dumps({
        "host": "example.org", "username": "ubuntu", "password": "specific",
    }))
    attempts = connection_options(resolve_target("SSH_LOCAL_PASSWORD"))
    assert next(attempts)["password"] == "specific"
    with pytest.raises(ConfigurationError, match="SSH_KEYS must"):
        next(attempts)
