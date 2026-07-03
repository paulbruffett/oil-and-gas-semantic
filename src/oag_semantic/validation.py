"""MetricFlow manifest-validation gate (ADR 0011).

Parses and validates the OSI semantic manifest with ``dbt-semantic-interfaces`` -- MetricFlow's
schema + validation rules -- checking it is well-formed: joins resolve, grain is consistent,
measures are additive, metrics reference real measures. This is a *dev/test-time* gate: it imports
the dev-only validator and is never on the answer-time path (compile/agent use ``manifest.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oag_semantic.manifest import SEMANTIC_DIR


@dataclass(frozen=True)
class ValidationResult:
    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_manifest(semantic_dir: str | Path = SEMANTIC_DIR) -> ValidationResult:
    """Validate the manifest with MetricFlow; returns collected error/warning messages.

    Import of dbt-semantic-interfaces is local so the module only pulls the dev dependency when
    the gate actually runs.
    """
    from dbt_semantic_interfaces.parsing.dir_to_model import (
        parse_directory_of_yaml_files_to_semantic_manifest,
    )
    from dbt_semantic_interfaces.validations.semantic_manifest_validator import (
        SemanticManifestValidator,
    )

    build = parse_directory_of_yaml_files_to_semantic_manifest(str(semantic_dir))
    issues = SemanticManifestValidator().validate_semantic_manifest(build.semantic_manifest)
    return ValidationResult(
        errors=[i.message for i in issues.errors],
        warnings=[i.message for i in issues.warnings],
    )
