"""Single JSON HOST_<NAME> profile configuration contracts."""
import base64
import json

import asyncssh
import pytest

from libersh.config import (
    ConfigurationError, connection_options, get_public_keys, list_hosts, resolve_target,
)


def test_multiple_hosts_and_secret_free_discovery(monkeypatch):
    monkeypatch.setenv("HOST_PROD", json.dumps({
        "host": "192.0.2.10", "username": "ubuntu", "port": 2222,
        "password": "prod-secret", "desc": "Production server",
    }))
    monkeypatch.setenv("HOST_DEV", json.dumps({
        "host": "dev.example.org", "username": "root", "password": "dev-secret",
    }))
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/ssh-agent.sock")
    target = resolve_target("HOST_PROD")
    assert (target.hostname, target.username, target.port) == (
        "192.0.2.10", "ubuntu", 2222)
    assert list(connection_options(target))[0]["password"] == "prod-secret"
    assert list(connection_options(target))[0]["known_hosts"] is None
    hosts = list_hosts()
    assert {x["name"] for x in hosts["hosts"]} == {"HOST_DEV", "HOST_PROD"}
    assert next(x for x in hosts["hosts"] if x["name"] == "HOST_PROD")["desc"] == "Production server"
    assert "prod-secret" not in repr(hosts)
    assert "dev-secret" not in repr(hosts)


def test_private_key(monkeypatch):
    key = asyncssh.generate_private_key("ssh-ed25519")
    monkeypatch.setenv("HOST_KEYED", json.dumps({
        "host": "example.org", "username": "ubuntu",
        "private_key": key.export_private_key().decode("utf-8"),
    }))
    target = resolve_target("HOST_KEYED")
    options = list(connection_options(target))[0]
    assert len(options["client_keys"]) == 1
    assert options["known_hosts"] is None
    assert "private_key" not in repr(list_hosts())


def test_verified_known_hosts_optional(monkeypatch):
    key = asyncssh.generate_private_key("ssh-ed25519")
    public = key.export_public_key().decode().strip()
    monkeypatch.setenv("HOST_VERIFIED", json.dumps({
        "host": "example.org", "username": "tester", "password": "secret",
        "known_hosts": "example.org " + public,
    }))
    assert list(connection_options(resolve_target("HOST_VERIFIED")))[0]["known_hosts"] is not None


@pytest.mark.parametrize("value,match", [
    ('not-json', "JSON object"),
    ('[]', "JSON object"),
    ('{"host":"example.org","username":"root","port":true}', "port"),
    ('{"host":"example.org","username":"root","port":65536}', "port"),
    ('{"host":"","username":"root","password":"x"}', "host"),
    ('{"host":"example.org","username":""}', "username"),
])
def test_invalid_configuration(monkeypatch, value, match):
    monkeypatch.setenv("HOST_BAD", value)
    with pytest.raises(ConfigurationError, match=match):
        resolve_target("HOST_BAD")


def test_missing_or_invalid_credentials(monkeypatch):
    monkeypatch.setenv("HOST_EMPTY", json.dumps({
        "host": "example.org", "username": "root",
    }))
    with pytest.raises(ConfigurationError, match="private_key, password or SSH_KEYS"):
        list(connection_options(resolve_target("HOST_EMPTY")))
    monkeypatch.setenv("HOST_EMPTY", json.dumps({
        "host": "example.org", "username": "root", "private_key": "not-a-key",
    }))
    with pytest.raises(ConfigurationError, match="Invalid private key"):
        list(connection_options(resolve_target("HOST_EMPTY")))


def test_direct_hostname_requires_ssh_url(monkeypatch):
    monkeypatch.setenv("HOST_PROD", json.dumps({
        "host": "example.org", "username": "ubuntu", "password": "secret",
    }))
    with pytest.raises(ConfigurationError, match=r"HOST_<NAME> or ssh://username@hostname"):
        resolve_target("example.org")
    with pytest.raises(ConfigurationError, match="dynamic SSH URLs use SSH_KEYS"):
        resolve_target("45.32.51.15")
    with pytest.raises(ConfigurationError, match="not configured"):
        resolve_target("HOST_UNKNOWN")


def test_global_keys_only_and_public_key_export(monkeypatch):
    first = asyncssh.generate_private_key("ssh-ed25519")
    second = asyncssh.generate_private_key("ssh-ed25519")
    monkeypatch.setenv("SSH_KEYS", json.dumps({
        "first": first.export_private_key().decode(),
        "second": second.export_private_key().decode(),
    }))
    monkeypatch.setenv("HOST_BARE", json.dumps({
        "host": "example.org", "username": "ubuntu",
    }))
    opts = list(connection_options(resolve_target("HOST_BARE")))
    assert len(opts) == 1
    assert opts[0]["client_keys"][0].export_public_key() == first.export_public_key()
    assert opts[0]["client_keys"][1].export_public_key() == second.export_public_key()
    assert opts[0]["agent_path"] is None and opts[0]["config"] is None
    assert {x["name"] for x in list_hosts()["hosts"]} >= {"HOST_BARE"}
    assert all(x["name"].startswith("HOST_") for x in list_hosts()["hosts"])
    public = get_public_keys()
    assert [x["name"] for x in public["keys"]] == ["first", "second"]
    assert public["keys"][0]["public_key"] == first.export_public_key().decode().strip()
    assert "PRIVATE KEY" not in repr(public)


def test_ordered_host_private_key_password_then_global(monkeypatch):
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    global_key = asyncssh.generate_private_key("ssh-ed25519")
    monkeypatch.setenv("SSH_KEYS", json.dumps({"main": global_key.export_private_key().decode()}))
    monkeypatch.setenv("HOST_ORDER", json.dumps({
        "host": "example.org", "username": "root",
        "private_key": host_key.export_private_key().decode(),
        "password": "host-password",
    }))
    opts = list(connection_options(resolve_target("HOST_ORDER")))
    assert len(opts) == 3
    assert opts[0]["client_keys"][0].export_public_key() == host_key.export_public_key()
    assert "password" not in opts[0]
    assert opts[1]["client_keys"] is None
    assert opts[1]["password"] == "host-password"
    assert "password" not in opts[2]
    assert opts[2]["client_keys"][0].export_public_key() == global_key.export_public_key()
    assert all(x["agent_path"] is None and x["config"] is None for x in opts)
    assert all(x["client_certs"] == [] for x in opts)
    public = get_public_keys("HOST_ORDER")["keys"]
    assert [x["name"] for x in public] == ["main", "HOST_ORDER"]
    assert public[-1]["public_key"] == host_key.export_public_key().decode().strip()
    assert "host-password" not in repr(public)


def test_invalid_global_key_configuration(monkeypatch):
    monkeypatch.setenv("HOST_BARE", json.dumps({
        "host": "example.org", "username": "root",
    }))
    monkeypatch.setenv("SSH_KEYS", "not-json")
    with pytest.raises(ConfigurationError, match="SSH_KEYS must"):
        list(connection_options(resolve_target("HOST_BARE")))
    monkeypatch.setenv("SSH_KEYS", json.dumps({"broken": "not-a-private-key"}))
    with pytest.raises(ConfigurationError, match="Invalid private key"):
        list(connection_options(resolve_target("HOST_BARE")))


def test_global_key_loading_is_lazy(monkeypatch):
    monkeypatch.setenv("SSH_KEYS", "invalid-global-keys")
    monkeypatch.setenv("HOST_LOCAL_PASSWORD", json.dumps({
        "host": "example.org", "username": "ubuntu", "password": "specific",
    }))
    attempts = connection_options(resolve_target("HOST_LOCAL_PASSWORD"))
    assert next(attempts)["password"] == "specific"
    with pytest.raises(ConfigurationError, match="SSH_KEYS must"):
        next(attempts)


@pytest.mark.parametrize("uri,hostname,username,port", [
    ("ssh://ubuntu@example.org", "example.org", "ubuntu", 22),
    ("ssh://root@203.0.113.42:2222", "203.0.113.42", "root", 2222),
    ("ssh://ubuntu@[2001:db8::1]", "2001:db8::1", "ubuntu", 22),
    ("ssh://admin@[2001:db8::2]:2200", "2001:db8::2", "admin", 2200),
])
def test_dynamic_ssh_url(uri, hostname, username, port):
    target = resolve_target(uri)
    assert (target.hostname, target.username, target.port) == (hostname, username, port)
    assert target.name == uri


@pytest.mark.parametrize("uri", [
    "ssh://example.org",  # username required
    "ssh://ubuntu@",  # host required
    "ssh://ubuntu:secret@example.org",  # no URL passwords
    "ssh://ubuntu@example.org:0",
    "ssh://ubuntu@example.org:65536",
    "ssh://ubuntu@example.org:nope",
    "ssh://ubuntu@example.org/path",
    "ssh://ubuntu@example.org?key=foo",
    "ssh://ubuntu@example.org#fragment",
    "ssh://ubuntu@[2001:db8::1",  # unclosed IPv6
])
def test_dynamic_ssh_url_invalid(uri):
    with pytest.raises(ConfigurationError):
        resolve_target(uri)


def test_dynamic_host_uses_only_global_keys(monkeypatch):
    main = asyncssh.generate_private_key("ssh-ed25519")
    monkeypatch.setenv("SSH_KEYS", json.dumps({
        "main": main.export_private_key().decode("utf-8"),
    }))
    monkeypatch.setenv("HOST_PROD", json.dumps({
        "host": "example.org", "username": "ubuntu",
        "password": "private-password",
    }))
    target = resolve_target("ssh://ubuntu@example.org")
    options = list(connection_options(target))
    assert len(options) == 1
    assert options[0]["client_keys"][0].export_public_key() == main.export_public_key()
    assert "password" not in options[0]
    assert options[0]["agent_path"] is None and options[0]["config"] is None
    assert options[0]["known_hosts"] is None
    assert get_public_keys(target.name)["keys"][0]["name"] == "main"
    assert "private-password" not in repr(options)
    monkeypatch.delenv("SSH_KEYS")
    with pytest.raises(ConfigurationError, match="SSH_KEYS"):
        list(connection_options(target))


def test_single_line_private_key_body_global_and_host(monkeypatch):
    key = asyncssh.generate_private_key("ssh-ed25519")
    full = key.export_private_key().decode()
    body = "".join(full.splitlines()[1:-1])
    assert "\n" not in body and "BEGIN" not in body and "END" not in body
    monkeypatch.setenv("SSH_KEYS", json.dumps({"primary": body}))
    monkeypatch.setenv("HOST_BODY", json.dumps({
        "host": "example.org", "username": "ubuntu", "private_key": body,
    }))
    attempts = list(connection_options(resolve_target("HOST_BODY")))
    assert len(attempts) == 2
    assert all(a["client_keys"][0].export_public_key() == key.export_public_key()
               for a in attempts)
    exported = get_public_keys("HOST_BODY")["keys"]
    assert [entry["source"] for entry in exported] == ["global", "host"]
    assert all(entry["public_key"] == key.export_public_key().decode().strip()
               for entry in exported)


def test_invalid_single_line_private_key_does_not_leak(monkeypatch):
    monkeypatch.setenv("SSH_KEYS", json.dumps({"bad": "not-valid-base64-SECRET"}))
    with pytest.raises(ConfigurationError, match="Invalid private key in SSH_KEYS") as exc:
        get_public_keys()
    assert "not-valid-base64-SECRET" not in str(exc.value)


@pytest.mark.parametrize("algorithm,format_name", [
    ("ssh-rsa", "pkcs1-der"),
    ("ssh-rsa", "pkcs8-der"),
    ("ecdsa-sha2-nistp256", "pkcs1-der"),
    ("ecdsa-sha2-nistp256", "pkcs8-der"),
    ("ssh-ed25519", "pkcs8-der"),
])
def test_der_base64_private_key_global_and_host(monkeypatch, algorithm, format_name):
    key = asyncssh.generate_private_key(algorithm)
    body = base64.b64encode(key.export_private_key(format_name)).decode("ascii")
    monkeypatch.setenv("SSH_KEYS", json.dumps({"global": body}))
    monkeypatch.setenv("HOST_DER", json.dumps({
        "host": "example.org", "username": "ubuntu", "private_key": body,
    }))
    attempts = list(connection_options(resolve_target("HOST_DER")))
    assert len(attempts) == 2
    assert all(attempt["client_keys"][0].export_public_key() == key.export_public_key()
               for attempt in attempts)
    exported = get_public_keys("HOST_DER")["keys"]
    assert [item["source"] for item in exported] == ["global", "host"]
    assert all(item["public_key"] == key.export_public_key().decode().strip()
               for item in exported)


@pytest.mark.parametrize("format_name", ["pkcs1-pem", "pkcs8-pem"])
def test_pem_body_and_full_pem_still_supported(monkeypatch, format_name):
    key = asyncssh.generate_private_key("ssh-rsa")
    complete = key.export_private_key(format_name).decode("ascii")
    body = "".join(complete.splitlines()[1:-1])
    monkeypatch.setenv("SSH_KEYS", json.dumps({"from_pem_body": body, "full_pem": complete}))
    keys = get_public_keys()["keys"]
    assert len(keys) == 2
    assert all(item["public_key"] == key.export_public_key().decode().strip()
               for item in keys)


def test_encrypted_pkcs8_der_base64_host_passphrase(monkeypatch):
    key = asyncssh.generate_private_key("ssh-ed25519")
    body = base64.b64encode(
        key.export_private_key("pkcs8-der", passphrase="secret-phrase")
    ).decode("ascii")
    monkeypatch.setenv("HOST_ENCRYPTED", json.dumps({
        "host": "example.org", "username": "ubuntu",
        "private_key": body, "passphrase": "secret-phrase",
    }))
    target = resolve_target("HOST_ENCRYPTED")
    assert next(connection_options(target))["client_keys"][0].export_public_key() == key.export_public_key()
    assert get_public_keys("HOST_ENCRYPTED")["keys"][0]["public_key"] == key.export_public_key().decode().strip()
    monkeypatch.setenv("HOST_ENCRYPTED", json.dumps({
        "host": "example.org", "username": "ubuntu",
        "private_key": body, "passphrase": "wrong",
    }))
    with pytest.raises(ConfigurationError, match="Invalid private key"):
        list(connection_options(resolve_target("HOST_ENCRYPTED")))


def test_malformed_der_base64_reports_safe_error(monkeypatch):
    invalid = base64.b64encode(b"not-an-ASN1-private-key").decode("ascii")
    monkeypatch.setenv("SSH_KEYS", json.dumps({"broken": invalid}))
    with pytest.raises(ConfigurationError, match="Invalid private key in SSH_KEYS") as exc:
        get_public_keys()
    assert invalid not in str(exc.value)
