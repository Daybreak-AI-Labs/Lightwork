# Restore one recorded state

Use a fresh recovery clone for each state. Do not apply these patches to current main or an existing working checkout. Different states can overlap or conflict.

1. Download the complete archive directory, including the bundle and `states/`. On Windows, use a short destination such as `C:\lw-recovery` and enable `core.longpaths=true` in a Git clone before checking out this archive. For example, use `git clone --no-checkout`, then `git config core.longpaths true`, then check out the preservation branch. This changes only that clone's configuration. Run `python verify_archive.py` from the archive directory. The verifier supports Windows extended paths and checks the files listed in `SHA256SUMS`; any mismatch is a stop condition.
2. Read `manifest.json` and choose the exact `state` entry. Record its `base_head` and its listed staged patch, unstaged patch, and untracked files. Entries containing only exclusions do not represent a new implementation.
3. Make a fresh clone of the private Lightwork repository without checking out files, then disable automatic line-ending conversion before the first checkout. Fetch the remote history needed by the bundle, including historical pull-request heads:

   ```console
   git clone --no-checkout https://github.com/Daybreak-AI-Labs/Lightwork.git recovered-lightwork
   cd recovered-lightwork
   git config core.autocrlf false
   git config core.longpaths true
   git fetch --tags origin
   git fetch origin "+refs/pull/*/head:refs/remotes/recovery-pr/*"
   git bundle verify "ABSOLUTE_PACKAGE_PATH/local-commits.bundle"
   git fetch "ABSOLUTE_PACKAGE_PATH/local-commits.bundle" "refs/local-preservation/*:refs/recovery/*"
   git switch --detach BASE_HEAD_FROM_MANIFEST
   ```

   Replace the uppercase placeholders. The bundle intentionally excludes history already available from GitHub; a missing-prerequisite error must be resolved before proceeding. The exact refs observed during preparation are recorded in `remote-ref-evidence.txt`. A successful bundle verification in the preparation environment is not a substitute for verification in this fresh clone.

4. Apply only the nonempty patches listed for the chosen state, in this order:

   ```console
   git apply --check --index "ABSOLUTE_PACKAGE_PATH/states/STATE_ID/staged.patch"
   git apply --index "ABSOLUTE_PACKAGE_PATH/states/STATE_ID/staged.patch"
   git apply --check "ABSOLUTE_PACKAGE_PATH/states/STATE_ID/unstaged.patch"
   git apply "ABSOLUTE_PACKAGE_PATH/states/STATE_ID/unstaged.patch"
   ```

   Skip a pair of commands when that patch is absent from the manifest. Do not use `--index` for the unstaged patch: keeping those changes outside the index reproduces their recorded staging state. The patches include binary changes when Git emitted them.

5. Copy the chosen state's `untracked/` contents into the new clone, retaining each manifest `restore_path`, including dotfiles. Leave these files untracked until you intentionally decide what to commit. Do not copy the recovery manifests into the product tree.
6. Inspect `git status`, `git diff --cached`, and `git diff`. Check restored untracked-file hashes against the manifest. Review the state for relevance and run appropriate validation before promoting any recovered source into active development.

The original local checkout paths are deliberately absent from this published package. A private workstation mapping was retained separately during preparation. The package is a snapshot at the recorded capture times; later changes and ignored local data remain outside its coverage. Preserve the originals until restoration and exclusion review are complete.
