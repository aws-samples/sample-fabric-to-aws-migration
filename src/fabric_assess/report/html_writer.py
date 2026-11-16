"""HTML report writer — renders the Jinja2 combined template.

The template ships as package data (see pyproject [tool.setuptools.package-data]).
jinja2 is imported lazily so the rest of the package imports without it; the CLI
depends on it, but scoring/bundle/model consumers do not.
"""
from __future__ import annotations

import os

from fabric_assess.models import Assessment
from fabric_assess.report._serialize import assessment_to_dict

_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
_TEMPLATE_NAME = "combined.html.j2"


class HtmlWriter:
    """Render an Assessment to a single self-contained HTML file."""

    def write(self, assessment: Assessment, out_dir: str) -> str:
        try:
            from jinja2 import Environment, FileSystemLoader, select_autoescape
        except ImportError as exc:  # pragma: no cover - env dependent
            raise RuntimeError(
                "jinja2 is required to render the HTML report; install it or use JSON output."
            ) from exc

        env = Environment(
            loader=FileSystemLoader(_TEMPLATE_DIR),
            autoescape=select_autoescape(["html", "j2"]),
        )
        template = env.get_template(_TEMPLATE_NAME)
        html = template.render(a=assessment_to_dict(assessment))

        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "assessment.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        return path
