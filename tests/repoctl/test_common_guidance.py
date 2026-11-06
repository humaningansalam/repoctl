from pathlib import Path


COMMON_GUIDANCE = (
    "README.md",
    "docs/contracts/template-repoctl-separation.md",
    "tests/fixtures/template-contract-1.0.0/AGENTS.md",
)

FORBIDDEN_HARNESS_TEXT = (
    "gpt-6-astra",
    "terra",
    "product execution handoff",
    "external completion",
    "expert director",
    "main handles product judgment",
    "same coordinator",
    "codex queue",
    "sessionplane collects",
    "hostspan waits",
)


def test_common_guidance_is_model_and_orchestrator_neutral() -> None:
    root = Path(__file__).resolve().parents[2]
    hits: list[str] = []
    for relative in COMMON_GUIDANCE:
        text = (root / relative).read_text(encoding="utf-8").lower()
        for forbidden in FORBIDDEN_HARNESS_TEXT:
            if forbidden in text:
                hits.append(f"{relative}: {forbidden}")
    assert not hits
    separation = (root / "docs/contracts/template-repoctl-separation.md").read_text(encoding="utf-8").lower()
    assert "model selection, orchestration, code editing" in separation
    assert "agent hierarchy" in (root / "README.md").read_text(encoding="utf-8").lower()
