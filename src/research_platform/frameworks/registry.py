from __future__ import annotations

from importlib.resources import files
from typing import Any

import yaml


def load_framework_registry() -> dict[str, Any]:
    framework_file = files("research_platform.frameworks").joinpath("frameworks.yaml")
    return yaml.safe_load(framework_file.read_text(encoding="utf-8"))

