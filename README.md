# Libersh

**Freedom to execute.** A Linux-first SSH/SFTP MCP for trusted AI agents, designed for Prefect Horizon.

Libersh exposes SSH capabilities allowed by the configured account. There are no command blacklists, path policies, or hidden approval steps. Authentication and access are enforced by Horizon, SSH, and the remote operating system; the MCP retains protocol integrity, optional SSH host-key verification, and bounded tool responses.

## Deployment

- Python 3.12+ with `pyproject.toml` autodetection
- Horizon entrypoint: **`mcp_server.py:mcp`**
- Run locally: `python -m pip install -e . && python mcp_server.py`
- Streamable HTTP, stateless MCP instances; set `PORT` if necessary

### SSH hosts (one environment variable per host)

Configure one Horizon environment variable whose **name** is the host alias and
whose **value** is a JSON object with everything needed to connect:

```text
SSH_PROD={"host":"203.0.113.10","username":"ubuntu","password":"your-password"}
```

For another server, simply add a different variable, such as `SSH_DEV`.
The agent selects a host with `host="SSH_PROD"`. `list_hosts` discovers
configured aliases without returning passwords or private keys.

Alternatively use a private key in the same JSON object:

```json
{"host":"203.0.113.10","username":"ubuntu","private_key":"-----BEGIN OPENSSH PRIVATE KEY-----\\n...\\n-----END OPENSSH PRIVATE KEY-----\\n"}
```

Required fields: `host`, `username`, and **either** `password` or `private_key`.
Optional fields: `port` (default `22`), `passphrase` (encrypted private key),
`desc` (label shown in `list_hosts`), and `known_hosts` (verified OpenSSH host-key
entries). Use JSON `\\n` escapes for multiline private keys or known_hosts
values, not literal unescaped line breaks.

Without `known_hosts`, SSH server identity is **not verified**. This lowers
configuration friction but allows an active network attacker to impersonate
the server. Add `known_hosts` when SSH server authenticity matters.
This is independent of remote command authorization.

The legacy `SSH_HOST`, `SSH_USERNAME`, `SSH_PASSWORD`, `SSH_IDENTITIES`,
and `SSH_HOSTS` formats have been removed rather than maintained as parallel
configuration systems. Tool arguments no longer accept `username`, `port`,
or `identity` overrides; these belong to each host's JSON object.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_hosts` | Configured SSH_* host aliases |
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

Each job is stored under `/var/tmp/libersh.<uid>.<32-hex-id>/` (override the parent with `LIBERSH_JOB_BASE`). No remote Python environment, daemon, Redis, or long-lived Horizon connection is required.

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
