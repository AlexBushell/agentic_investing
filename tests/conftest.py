import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: tests that read from data/downloads/ and require downloaded XHTML files",
    )
