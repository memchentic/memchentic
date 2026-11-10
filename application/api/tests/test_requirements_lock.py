"""The committed lock files pin every dependency by hash and match the image."""

from __future__ import annotations

import re
from importlib import metadata
from pathlib import Path

import pytest
from packaging.version import Version

_API_ROOT = Path(__file__).resolve().parents[1]
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s;\\]+)")


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pins(lock_name: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in (_API_ROOT / lock_name).read_text(encoding="utf-8").splitlines():
        match = _PIN.match(line)
        if match:
            pins[_canonical(match.group(1))] = match.group(2)
    return pins


@pytest.mark.parametrize("lock_name", ["requirements.lock", "requirements-dev.lock"])
def test_lock_file_pins_every_requirement_with_a_hash(lock_name: str) -> None:
    text = (_API_ROOT / lock_name).read_text(encoding="utf-8")
    pins = _pins(lock_name)
    assert len(pins) >= 50, f"{lock_name} looks truncated: {len(pins)} pins"
    assert "setuptools" in pins, "the build backend must be locked (requirements-build.in)"
    # One requirement block is a pin line followed by its --hash lines.
    blocks = re.split(r"\n(?=[A-Za-z0-9])", text)
    unhashed = [b.splitlines()[0] for b in blocks if _PIN.match(b) and "--hash=sha256:" not in b]
    assert not unhashed, f"requirements without hashes in {lock_name}: {unhashed}"


def test_dev_lock_is_a_superset_of_the_runtime_lock() -> None:
    runtime = _pins("requirements.lock")
    dev = _pins("requirements-dev.lock")
    different = {n: (v, dev.get(n)) for n, v in runtime.items() if dev.get(n) != v}
    assert not different, f"runtime pins missing or different in the dev lock: {different}"
    for tool in ("pytest", "pytest-asyncio", "ruff"):
        assert tool in dev, f"{tool} missing from requirements-dev.lock"
        assert tool not in runtime, f"{tool} must not be in requirements.lock"


def test_installed_packages_match_the_lock() -> None:
    dev = _pins("requirements-dev.lock")
    installed = {
        _canonical(dist.metadata["Name"]): dist.version
        for dist in metadata.distributions()
        if dist.metadata["Name"]
    }
    mismatched = {
        name: (installed[name], pinned)
        for name, pinned in dev.items()
        if name in installed and Version(installed[name]) != Version(pinned)
    }
    assert not mismatched, f"installed != locked, as (installed, locked): {mismatched}"
