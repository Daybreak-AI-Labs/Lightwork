"""Verify recovery-package bytes without modifying files."""
import hashlib
from pathlib import Path

root = Path(__file__).resolve().parent
checked = 0
errors = []
for line in (root / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
    expected, name = line.split("  ", 1)
    target = (root / name).resolve()
    if not target.is_relative_to(root):
        errors.append(f"Unsafe path: {name}")
        continue
    if not target.is_file():
        errors.append(f"Missing: {name}")
        continue
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != expected:
        errors.append(f"Hash mismatch: {name}")
    checked += 1
if errors:
    raise SystemExit("\n".join(errors))
print(f"Verified {checked} files.")
