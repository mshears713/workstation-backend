"""One-off smoke test: invoke the compiled graph directly against the real
OpenRouter/Nemotron model (no FastAPI, no Studio). Prints and saves the
final state so the run can be inspected."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.graph.graph import graph  # noqa: E402

if __name__ == "__main__":
    run_id = f"smoke-{int(time.time())}"
    start = time.monotonic()
    result = graph.invoke(
        {"run_id": run_id, "notes": "Direct smoke test, real Nemotron model."},
        config={"run_name": f"smoke-{run_id}", "tags": ["smoke-test"]},
    )
    elapsed = time.monotonic() - start

    out_path = Path(__file__).resolve().parent.parent / "data" / f"{run_id}.smoke.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"elapsed_seconds={elapsed:.1f}")
    print(f"final_disposition={result.get('final', {}).get('disposition')}")
    print(f"saved={out_path}")
