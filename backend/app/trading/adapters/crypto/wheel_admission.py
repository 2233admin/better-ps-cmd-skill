"""Machine-readable admission policy for crypto GitHub wheels."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WheelAdmissionPolicy:
    default_decision: str
    allowed_roles: tuple[str, ...]
    reference_only_roles: tuple[str, ...]
    banned_capabilities: tuple[str, ...]
    required_checks: tuple[str, ...]
    admissions: dict[str, dict[str, Any]]

    def decision_for(self, package: str) -> str:
        item = self.admissions.get(package.lower())
        return str(item.get("decision", self.default_decision)) if item else self.default_decision

    def assert_allowed_for_runtime(self, package: str) -> None:
        decision = self.decision_for(package)
        if decision != "allow":
            raise ValueError(f"crypto wheel {package} is {decision}; runtime imports require allow")


def load_wheel_admission_policy(path: Path | None = None) -> WheelAdmissionPolicy:
    policy_path = path or _default_policy_path()
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    return WheelAdmissionPolicy(
        default_decision=str(payload["default_decision"]),
        allowed_roles=tuple(payload["allowed_roles"]),
        reference_only_roles=tuple(payload["reference_only_roles"]),
        banned_capabilities=tuple(payload["banned_capabilities"]),
        required_checks=tuple(payload["required_checks"]),
        admissions={str(item["name"]).lower(): item for item in payload["admissions"]},
    )


def _default_policy_path() -> Path:
    return Path(__file__).resolve().parents[5] / "docs" / "CRYPTO_WHEEL_ADMISSION.json"
