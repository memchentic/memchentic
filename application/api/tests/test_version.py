"""The package version has one source: pyproject.toml, read through the installed metadata."""

from importlib.metadata import version

import memchentic


def test_version_has_one_source() -> None:
    """``__version__`` and the OpenAPI version equal the installed package version."""
    # Imported here, like tests/conftest.py does, so the app wiring sees the test settings.
    from memchentic.main import create_app

    installed = version("memchentic")
    assert memchentic.__version__ == installed
    assert create_app().version == installed
