"""Evaluate `stale` against a labeled benchmark.

Benchmark: JSON array of {number, decision: current|superseded, note}.
A consulta counts as predicted positive when its status is possibly_stale.

    uv run python scripts/eval_stale.py BENCHMARK.json [--verbose]
"""

import argparse
import json
from pathlib import Path

from dgt_consultas.boe import BOE
from dgt_consultas.search import Index
from dgt_consultas.stale import StaleChecker


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("benchmark", type=Path)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    gold = json.loads(args.benchmark.read_text(encoding="utf-8"))
    corpus = Path(__file__).resolve().parent.parent
    checker = StaleChecker(Index(corpus), BOE(corpus / "index" / "boe", offline=True))
    tp = fp = fn = tn = 0
    for item in gold:
        r = checker.check(item["number"])
        pred = r.status == "possibly_stale"
        gold_pos = item["decision"] == "superseded"
        tp += pred and gold_pos
        fp += pred and not gold_pos
        fn += gold_pos and not pred
        tn += not pred and not gold_pos
        tag = {
            (True, True): "TP",
            (True, False): "FP",
            (False, True): "FN",
            (False, False): "TN",
        }[(pred, gold_pos)]
        if args.verbose or tag in ("FP", "FN"):
            print(f"{tag} {r.number} {r.status} signals={len(r.signals)}")
            for s in r.signals[:6]:
                print(
                    f"     {getattr(s, 'severity', '-'):20s} {s.law} | {s.block} | {s.effective} | {s.evidence[-110:]}"
                )
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    print(
        f"\nTP={tp} FP={fp} FN={fn} TN={tn}  precision={precision:.2f} recall={recall:.2f}"
    )


if __name__ == "__main__":
    main()
