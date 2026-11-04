# Libersh

**Freedom to execute.** A Linux-first SSH/SFTP MCP for trusted AI agents, designed for Prefect Horizon.

Libersh exposes SSH capabilities allowed by the configured account. There are no command blacklists, path policies, or hidden approval steps. Authentication and access are enforced by Horizon, SSH, and the remote operating system; the MCP retains protocol integrity, optional SSH host-key verification, and bounded tool responses.

## Deployment

- Python 3.12+ with `pyproject.toml` autodetection
- Horizon entrypoint: **`mcp_server.py:mcp`**
- Run locally: `python -m pip install -e . && python mcp_server.py`
- Streamable HTTP, stateless MCP instances; set `PORT` if necessary

### Host profiles, dynamic SSH URLs, and global private keys

Each host is one Horizon JSON environment variable, named after the host alias:

```text
HOST_PROD={"host":"203.0.113.10","username":"ubuntu","password":"your-password"}
HOST_DEV={"host":"dev.example.org","username":"root"}
```

Host fields: `host` and `username` (required), `port` (default `22`),
`private_key`, `password`, `passphrase` (for host-encrypted private keys),
`known_hosts`, and `desc` (all optional).

An additional environment variable contains multiple named **global private keys**:

```text
SSH_KEYS={"main":"-----BEGIN OPENSSH PRIVATE KEY-----\\n...\\n-----END OPENSSH PRIVATE KEY-----\\n","backup":"-----BEGIN OPENSSH PRIVATE KEY-----\\n...\\n-----END OPENSSH PRIVATE KEY-----\\n"}
```

The value is a JSON object mapping names to OpenSSH private-key strings. Keys are
tried in JSON property insertion order. Use escaped `\\n` within JSON strings.
There is no global password storage; a password always belongs to a specific
host profile. No separate public-key storage is required.

**Authentication order** (skip absent credentials):

1. Host-specific `private_key`
2. Host-specific `password` (password or keyboard-interactive)
3. All `SSH_KEYS` private keys, in configured order

The stages make separate SSH connections after **authentication rejection**.
A transport failure, host-key error, or SSH disconnect does not trigger another
credential attempt. Many failed keys may exhaust the remote server's
`MaxAuthTries` limit. Libersh does not load `~/.ssh/config`, default key
files or SSH Agent identities; it only uses the environment-provided credentials.

For the public key corresponding to a stored private key, call the MCP tool
`get_public_keys()`. It returns OpenSSH-format **plaintext public keys** for
every `SSH_KEYS` entry, with its name. Call `get_public_keys(host="HOST_PROD")`
to also include that host's own `private_key` public key. Private keys,
passwords and passphrases are never returned.

**Server identity:** If a host profile omits `known_hosts`, SSH server
identity is **not verified**, allowing active network impersonation.
Supply verified OpenSSH known_hosts entries in the host JSON to enable it.
This is separate from account authentication and command authorization.

Use `host="HOST_PROD"` to select a configured host. For an ephemeral
VPS (for example, created with Vultr and provisioned with a public key
derived from `get_public_keys()`), pass a direct SSH URL to **any**
SSH/SFTP/job tool without creating a Horizon environment variable:

```text
exec(host="ssh://ubuntu@203.0.113.42", command="uname -a")
exec(host="ssh://root@203.0.113.42:2222", command="id")
read_file(host="ssh://ubuntu@[2001:db8::1]", path="/etc/os-release")
```

Dynamic SSH URLs require a username and host, with optional port
(default `22`). They use **only the global `SSH_KEYS` private keys**,
and never any host-specific password/key, local SSH config, key file,
or agent identity. URLs cannot include a password, file path or query.
Dynamic targets are not added to `list_hosts`, and because they have
no `known_hosts` profile, **server identity is not verified**.
Provision a target only when you intend to grant it SSH access with
the global public key(s). `list_hosts` discovers `HOST_*` aliases without
credentials. The separate `SSH_KEYS` variable is not a host profile. Tool arguments have no
`username`, `port`, or `identity` overrides.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_hosts` | Configured HOST_* host aliases |
| `get_public_keys` | Export OpenSSH public keys from global and optional host private keys |
| `exec` | One-shot SSH command; stdout and stderr separated, bounded output, exit status |
| `stat`, `list_dir` | SFTP metadata and directory entries |
| `read_file` | Byte-addressed SFTP page, UTF-8 or Base64 |
| `write_file` | SFTP overwrite, append, offset or POSIX atomic replace |
| `file_op` | mkdir/remove/rmdir/rename/posix_rename/chmod |
| `upload_file` | Stream ChatGPT-authorized attachment into SFTP (`openai/fileParams`) |
| `job_start`, `job_status` | Remote persistent job lifecycle |
| `job_output`, `job_list`, `job_stop` | Log pagination, discovery, and process-group termination |

`exec` collects at most 512 KiB per call, drains excess output, and reports truncation. A nonzero exit code is not an MCP error. A timeout or transport loss does **not** establish that the remote process has stopped. For long commands or large output use `job_start`, then `job_status`/`job_output`.

`upload_file` accepts ChatGPT's file reference (no large Base64 in model context), downloads from an HTTPS signed URL and uploads in bounded SFTP chunks. The source attachment cap is **512 MB (decimal)**. Direct writing can leave a partial target on error; `atomic_replace=true` writes a temporary file and uses the server's OpenSSH POSIX rename extension, failing if not supported. SHA-256 can be supplied for verification. Exporting remote files as client-downloadable attachments is not part of this initial release; use paginated `read_file` for inspection.

## Persistent Linux jobs

Each job is stored under `/var/tmp/libersh.<uid>.<32-hex-id>/` on the remote host. No remote Python environment, daemon, Redis, or long-lived Horizon connection is required.

```text
/var/tmp/libersh.1000.0123456789abcdef0123456789abcdef/
├── meta.json
├── command
├── runner.sh
├── pid
├── pgid
├── pid_start_ticks
├── boot_id
├── stdout
├── stderr
├── internal.log
└── exit
```

On Linux with util-linux, `nohup` + `setsid` detach the job; `flock --close` holds a BSD lock on the **directory** itself until the job runner exits. `systemd-tmpfiles` respects such directory locks and skips active jobs when applying its age policy. The lock is released automatically when the owner terminates. `exit` is written by same-directory rename only after collecting the command exit code. Jobs can be queried by any Horizon instance after SSH disconnect.

Use an existing `job_id` to retry **querying** a possibly launched job; a repeated `job_start` with the same ID never launches the command a second time. `job_stop` verifies boot ID, `/proc` PID start ticks and process group before signaling. `job_stop` is a request, not a promise that escaped child processes have terminated.

State `unknown` is intentional when a crash, SIGKILL, or disconnect prevents observation of the final exit status. Host reboot changes boot ID; existing job directories don't imply running processes. Job history retention depends on the host's `systemd-tmpfiles` policy: `/var/tmp` often has a ~30 day inactivity age, but this is **not** a contractual job retention period. There is no custom garbage collector.

**Known limits:** Linux (`/proc`, `setsid`, `flock`) is required for durable jobs. A host's systemd login-session policy may still terminate a detached job; verify on your target SSH environment. SFTP directory listings are not snapshot-stable. A direct attachment upload on a lost connection may leave a partial file; use atomic mode if the server supports it. Generated commands run under `/bin/sh`, independent of the account's interactive shell.

## Testing

`python -m pip install -e '.[test]' && pytest -q`

CI runs Python 3.12 and 3.13 on Linux. Tests cover config, MCP tool registration, real loopback SSH/SFTP, remote job state after disconnect, nonzero exit codes, binary paging, and the POSIX wrapper's directory lock. Production server execution is intentionally excluded from CI.
