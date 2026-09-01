#!/usr/bin/env python3
"""Probe prompt with multiple tokenizers; emit tokenization.json"""
from pathlib import Path
import sys, json, argparse
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.prompt.builder import PromptBuilder
from src.prompt.tokenizer_probe import probe_many

ap = argparse.ArgumentParser()
ap.add_argument("--tokenizers", nargs="+", default=["bert-base-uncased", "google-bert/bert-base-uncased"])
ap.add_argument("--transcript", type=str, default="hello")
ap.add_argument("--latest-action", type=str, default="ls -la")
ap.add_argument("--out", type=str, default="reports/tokenization.json")
args = ap.parse_args()

pb = PromptBuilder("prompts/original/policy.txt")
prompt, manifest = pb.build(transcript=args.transcript, latest_action=args.latest_action)
# extract dynamic section for probe
dynamic = pb.dynamic_template.format(transcript=args.transcript, metadata="", latest_action=args.latest_action)
results = probe_many(prompt, dynamic, args.tokenizers)
out = [r.__dict__ for r in results]
Path(args.out).parent.mkdir(parents=True, exist_ok=True)
Path(args.out).write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2))
