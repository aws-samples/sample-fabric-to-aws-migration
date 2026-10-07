"""JSON report writer — the machine-readable assessment artifact."""
from __future__ import annotations

import json
import os

from fabric_assess.models import Assessment
from fabric_assess.report._serialize import assessment_to_dict


class JsonWriter:
    """Write an Assessment as a pretty-printed JSON report."""

    def write(self, assessment: Assessment, out_dir: str) -> str:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "assessment.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(assessment_to_dict(assessment), f, ensure_ascii=False, indent=2)
        return path
