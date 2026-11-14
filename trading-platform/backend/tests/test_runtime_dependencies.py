"""Every module the running app imports must come from the runtime dependencies, not the test-only ones."""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
# Import names that differ from their package names.
ALIASES = {
    "jose": "python-jose",
    "yaml": "pyyaml",
    "psycopg": "psycopg",
    "argon2": "argon2-cffi",
    "pydantic_settings": "pydantic-settings",
}


def _runtime_packages() -> set[str]:
    names = set()
    for line in (ROOT / "requirements.lock").read_text().splitlines():
        match = re.match(r"^([A-Za-z0-9_.\-]+)==", line)
        if match:
            names.add(match.group(1).lower().replace("_", "-"))
    return names


def test_every_third_party_import_in_the_app_is_a_runtime_dependency():
    stdlib = set(sys.stdlib_module_names)
    runtime = _runtime_packages()
    missing = set()
    for path in (ROOT / "app").rglob("*.py"):
        for match in re.finditer(r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", path.read_text(), re.MULTILINE):
            module = match.group(1)
            if module in stdlib or module == "app":
                continue
            package = ALIASES.get(module, module).lower().replace("_", "-")
            if package not in runtime:
                missing.add(f"{module} (in {path.relative_to(ROOT)})")
    assert not missing, f"imported by the app but not in requirements.lock: {sorted(missing)}"
