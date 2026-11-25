# Libersh

Libersh gives trusted MCP agents SSH command execution, SFTP file access, and persistent Linux jobs. It runs on Prefect Horizon or as a standalone Streamable HTTP server.

Agents act with the permissions of the SSH account. Libersh adds no command blacklists, path restrictions, or approval steps; control access to the MCP endpoint through your hosting environment.

## Deployment

Requires **Python 3.12+**. On **Prefect Horizon**, use **`mcp_server.py:mcp`** as the entrypoint and set the environment variables described below.

To run from source, configure the SSH environment variables below before starting the server:

```sh
git clone https://github.com/ReallocAll/libersh.git
cd libersh
python -m pip install -e .
python mcp_server.py
```

Standalone mode listens on `0.0.0.0:8000`; set `PORT` to change the port.

## SSH configuration

### Named hosts

Each `HOST_*` environment variable contains one JSON host profile:

```text
HOST_DEV={"host":"dev.example.org","username":"ubuntu","password":"REPLACE_ME"}
```

Replace the example address and password. Only `host` and `username` are required fields; supply credentials in the profile or through `SSH_KEYS`.

| Field | Meaning |
| --- | --- |
| `host`, `username` | Remote hostname or IP address and SSH user |
| `port` | SSH port; defaults to `22` |
| `private_key`, `password` | Host-specific credentials |
| `passphrase` | Decrypts the host's `private_key` |
| `known_hosts` | Trusted OpenSSH known_hosts contents, not a file path |
| `desc` | Description returned by `list_hosts` |

### Private keys

To share private keys across hosts, set `SSH_KEYS` to a JSON object:

```text
SSH_KEYS={"main":"REPLACE_WITH_COMPLETE_PRIVATE_KEY"}
```

Both `private_key` and `SSH_KEYS` accept full PEM/OpenSSH text (use JSON `\n` escapes for newlines) or the complete single-line Base64 body without `BEGIN`/`END` markers. For legacy encrypted PEM, retain the full text and encryption headers. Encrypted keys require a host-specific `private_key` and `passphrase`; global keys must be usable without a passphrase.

Credentials are tried in this order: **host private key → host password → global keys in JSON order**. Only authentication rejection advances to the next stage; transport and host-key failures stop the attempt. Libersh does not load local SSH config, default key files, or SSH Agent identities.

`get_public_keys()` returns OpenSSH public keys for all global keys. Pass `host="HOST_DEV"` to also include that host's key. Private keys, passwords, and passphrases are not returned.

### Dynamic targets

Every SSH/SFTP/job tool also accepts `ssh://username@host[:port]` instead of a host alias. Examples use MCP tool-call notation:

```text
exec(host="ssh://ubuntu@203.0.113.42:2222", command="uname -a")
read_file(host="ssh://ubuntu@[2001:db8::1]", path="/etc/os-release")
```

Dynamic URLs use only `SSH_KEYS`, do not inherit host profiles, and are not listed by `list_hosts`. Omit passwords, file paths, queries, and fragments; the default port is `22`.

**Server identity is not verified** for dynamic URLs or profiles without `known_hosts`, allowing server impersonation. Use a named profile with verified OpenSSH known_hosts contents to enable verification.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_hosts` | Discover configured hosts without exposing credentials |
| `get_public_keys` | Derive public keys from stored private keys |
| `exec` | Run a command and collect stdout, stderr, and exit status |
| `stat`, `list_dir` | Inspect SFTP metadata and directory entries |
| `read_file` | Read a file page as UTF-8 or Base64 |
| `write_file` | Overwrite, append, write at an offset, or atomically replace a file |
| `file_op` | `mkdir`, `remove`, `rmdir`, `rename`, `posix_rename`, or `chmod` |
| `upload_file` | Stream a ChatGPT-authorized attachment to SFTP |
| `job_start`, `job_status` | Start a persistent job and query its state |
| `job_output`, `job_list`, `job_stop` | Read logs, discover jobs, and request termination |

### Commands and files

- **Execution:** `exec` keeps at most 512 KiB total across stdout and stderr and reports truncation. Its default timeout is 60 seconds. A nonzero exit code is a normal result; a timeout or lost connection does not prove the remote process stopped. Use `job_start` for long commands or large logs.
- **Pagination:** `read_file` and `job_output` use byte offsets. Continue with the returned `next_offset` while `has_more` is true; pages may shrink to fit the response budget or a UTF-8 boundary. Directory listings are not stable snapshots.
- **Uploads:** `upload_file` requires client support for `openai/fileParams` and accepts attachments up to **512 MB (decimal)**. Supply `expected_sha256` to verify content. Remote files can be inspected through `read_file`; there is no attachment-download tool.
- **Writes:** Direct writes can leave a partial file on failure. `write_file(mode="atomic_replace")` and `upload_file(atomic_replace=true)` require the server's SFTP POSIX rename extension.

## Persistent Linux jobs

Jobs require Linux with `/proc`, `/bin/sh`, `nohup`, and util-linux (`setsid`, `flock`). Job commands run under `/bin/sh`; no remote Python or additional daemon is needed.

State and logs live in `/var/tmp/libersh.<uid>.<job_id>/`, so another MCP instance can query jobs after SSH disconnect. Start a job, then use its returned `job_id`:

```text
job_start(host="HOST_DEV", command="sleep 60; uname -a")
job_status(host="HOST_DEV", job_id="<returned job_id>")
job_output(host="HOST_DEV", job_id="<returned job_id>", stream="stdout", offset=0)
```

- If launch is interrupted, query the same `job_id` before submitting a new job. Caller-supplied IDs must be 32 lowercase hex characters. Reusing an ID prevents another launch **while its job directory exists**.
- `unknown` means the final state could not be confirmed; it does not establish failure or termination. A host reboot may produce `interrupted`.
- `job_stop` sends TERM, then KILL if needed, to a verified process group. Processes that leave the group may survive.
- Host login-session policies can still terminate detached jobs. History retention follows the host's temporary-directory cleanup policy; there is no fixed retention period.

## Development

```sh
python -m pip install -e '.[test]'
python -m pytest -q
```

CI runs on Linux with Python 3.12 and 3.13, including loopback SSH/SFTP tests and job behavior after disconnect.

## License

[MIT](LICENSE)
