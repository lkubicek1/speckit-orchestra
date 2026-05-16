from __future__ import annotations

from speckit_orchestra.epics import Approval, Epic, EpicDocument, FeatureRef, Scope, Validation
from speckit_orchestra.reporting import render_summary_report


def test_summary_uses_blocker_suggested_next_action() -> None:
    epic = Epic(
        id="EPIC-001",
        title="Build widget",
        goal="Implement the widget flow.",
        tasks=["T001"],
        dependencies=[],
        risk="medium",
        parallelSafe=False,
        approval=Approval(required=False, reason=None),
        scope=Scope(include=["src/**"], exclude=[]),
        acceptance=["Widget flow works."],
        validation=Validation(commands=["pytest"]),
        stopConditions=["Requirements conflict."],
    )
    doc = EpicDocument(
        feature=FeatureRef(
            id="001-demo",
            path="specs/001-demo",
            spec="specs/001-demo/spec.md",
            plan="specs/001-demo/plan.md",
            tasks="specs/001-demo/tasks.md",
        ),
        epics=[epic],
    )
    state = {
        "status": "blocked",
        "epics": {
            "EPIC-001": {
                "status": "blocked",
                "blocker": {
                    "message": "Validation failed.",
                    "suggestedNextAction": "Run `speckit-orchestra resume specs/001-demo --allow-dirty`.",
                },
            }
        },
    }

    report = render_summary_report(doc, state)

    assert "Run `speckit-orchestra resume specs/001-demo --allow-dirty`." in report
