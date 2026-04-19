"""Convert pipeline / API payloads to JSON-serializable structures (no nan/inf, Enum -> value)."""

from __future__ import annotations

import enum
import math
from typing import Any


def json_safe(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(x) for x in obj]
    try:
        import numpy as np

        if isinstance(obj, np.generic):
            return json_safe(obj.item())
        if isinstance(obj, np.ndarray):
            return json_safe(obj.tolist())
    except ImportError:
        pass
    return str(obj)
