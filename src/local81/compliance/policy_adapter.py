from __future__ import annotations

from local81.policy import compliance_findings

from .catalog import get_rule
from .models import ComplianceFinding


def _status(level: str) -> str:
    return {"PASS": "pass", "WARN": "warn", "FAIL": "fail"}.get(level, "unknown")


def scan_access_policy() -> list[ComplianceFinding]:
    rule = get_rule("L26-ACCESS-001")
    findings: list[ComplianceFinding] = []
    for finding in compliance_findings():
        findings.append(
            ComplianceFinding(
                rule_id=rule.rule_id,
                title=rule.title,
                status=_status(finding.level),
                severity=rule.severity if finding.level != "PASS" else "info",
                category=rule.category,
                subject=finding.control,
                message=f"{finding.control}: {finding.detail}",
                controls=list(rule.controls),
                evidence=[finding.render()],
                remediation=rule.remediation,
                source="policy",
                confidence="high",
            )
        )
    return findings
