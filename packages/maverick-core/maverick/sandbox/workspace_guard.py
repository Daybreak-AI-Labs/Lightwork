"""Reject container mounts that would expose the operator's private home."""
from pathlib import Path


def validate_container_workspace(workdir: Path) -> None:
    from ..paths import maverick_home

    workspace = Path(workdir).resolve()
    home = Path.home().resolve()
    state = maverick_home().resolve()
    private_dirs = tuple((home / name).resolve() for name in (
        ".ssh", ".aws", ".config", ".gnupg", ".docker", ".kube",
    ))
    private_files = tuple((home / name).resolve() for name in (
        ".bashrc", ".zshrc", ".npmrc", ".pypirc", ".netrc", ".gitconfig",
    ))
    sensitive = (state, *private_dirs, *private_files)
    # The shipped workspace is deliberately below the state root; other state
    # descendants include databases, audit evidence and credentials.
    allowed_workspace = state / "workspace"
    if (any(path.is_relative_to(workspace) for path in sensitive)
            or any(workspace.is_relative_to(path) for path in private_dirs)
            or (workspace.is_relative_to(state) and not workspace.is_relative_to(allowed_workspace))):
        raise ValueError("Choose a dedicated workspace outside the operator's private data directories.")
