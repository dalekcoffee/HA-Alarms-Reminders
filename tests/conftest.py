"""Fixtures."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components in every test."""
    return


@pytest.fixture(autouse=True)
def media_dir(hass, tmp_path):
    """Keep sounds in a temp folder."""
    hass.config.media_dirs = {"local": str(tmp_path / "media")}
    return tmp_path / "media"
