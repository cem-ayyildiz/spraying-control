"""Fixtures for the Home Assistant integration tests.

Run these with the dedicated environment, which pins the Home Assistant test
harness:

    .venv-ha/bin/python -m pytest tests_ha
"""

import logging

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"

# The in-memory recorder echoes every statement at INFO; quiet it so test
# output stays readable.
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """Let Home Assistant load custom_components/spraying_control.

    ``recorder_mock`` is listed first on purpose: the integration declares
    recorder as a dependency, and the recorder fixture has to claim its
    database before anything instantiates ``hass``.
    """
    yield
