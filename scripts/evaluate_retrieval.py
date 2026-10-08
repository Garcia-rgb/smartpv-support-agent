"""Read-only local retrieval acceptance. No model API or web search is called."""

import argparse
import asyncio
import json
from pathlib import Path

from support_agent import __version__
from support_agent.config import get_settings
from support_agent.db import SessionFactory, engine
from support_agent.services.agent import SupportAgent


async def evaluate(cases_path: Path, output: Path) -> None:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    results = []
    async with SessionFactory() as db:
        agent = SupportAgent(db, get_settings())
        for case in cases:
            hits = await agent.retrieve(case["question"])
            selected = agent.evidence_hits(hits) if hasattr(agent, "evidence_hits") else hits[:2]
            # Match the deployed private-answer gate; weak raw candidates are not usable evidence.
            if not hits or hits[0].score < 0.35:
                selected = []
            source_ok = not case.get("expected_source") or any(
                case["expected_source"] in h.document.filename for h in selected
            )
            terms_ok = all(
                any(t in h.chunk.content for h in selected) for t in case.get("expected_terms", [])
            )
            passed = (
                not selected
                if case.get("expect_empty")
                else bool(selected) and source_ok and terms_ok
            )
            results.append(
                {
                    **case,
                    "passed": passed,
                    "selected_ids": [h.chunk.id for h in selected],
                    "hits": [
                        {
                            "id": h.chunk.id,
                            "source": h.document.filename,
                            "score": round(h.score, 4),
                            "excerpt": h.chunk.content[:200],
                        }
                        for h in hits
                    ],
                }
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "version": __version__,
                "mode": "local_retrieval_only",
                "passed": sum(r["passed"] for r in results),
                "total": len(results),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "version": __version__,
                "passed": sum(r["passed"] for r in results),
                "total": len(results),
                "report": str(output),
            },
            ensure_ascii=False,
        )
    )
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases", type=Path, default=Path("tests/fixtures/retrieval_acceptance.json")
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(evaluate(args.cases, args.output))
