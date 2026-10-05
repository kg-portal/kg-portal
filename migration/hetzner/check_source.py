"""Read-only source completeness check; does not import or start the CRM."""
import ast
import hashlib
import json
from pathlib import Path


def check(root):
    manifest = json.loads((root / "migration/hetzner/source_manifest.json").read_text())
    errors = []
    for name, expected in manifest["files"].items():
        path = root / name
        if not path.is_file():
            errors.append(f"MISSING: {name}")
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            errors.append(f"CHANGED: {name}")
        if path.suffix == ".py":
            try:
                ast.parse(path.read_text(encoding="utf-8-sig"), filename=name)
            except SyntaxError as exc:
                errors.append(f"SYNTAX: {name}:{exc.lineno}")
    return manifest, errors


if __name__ == "__main__":
    manifest, errors = check(Path(__file__).resolve().parents[2])
    for error in errors:
        print(error)
    print(f"Source files checked: {len(manifest['files'])}; errors: {len(errors)}")
    print("This checks source completeness only, not live data, secrets or integrations.")
    raise SystemExit(1 if errors else 0)
