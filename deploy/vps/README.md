# VPS service workspace

Run the pinned `install.sh` recipe. After setup, it invokes `render_service.py`
as the service account to read that account's `.maverick/config.toml`, create
the selected workspace and confirm it is writable. Only then does it publish
and enable the systemd unit. `WorkingDirectory` and `ReadWritePaths` use the
saved absolute workspace for local and container sandboxes; spaces and systemd
percent specifiers are escaped. With an SSH sandbox, the service instead uses
the local `.maverick/service-workspace` directory. The configured SSH workspace
remains a remote POSIX path and is neither expanded nor created on the VPS.
Setup requires an SSH destination (`user@host` or an SSH config alias), but
does not connect to it. Connectivity is checked when the SSH sandbox starts.

Do not copy `maverick.service` directly: it contains renderer placeholders.
If you change the saved workspace later, regenerate the unit through the
installer before restarting the service. A missing `.env` is allowed for a
setup completed without provider keys; configure a provider before running
provider-backed work.

Native Linux acceptance still requires checking `systemctl status maverick`
after start and confirming a harmless local workspace operation. Offline
renderer tests cannot establish systemd, filesystem ownership or mount policy
on a deployed VPS.
