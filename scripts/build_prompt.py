#!/usr/bin/env python3
"""Build exact classifier input from policy + dynamic fields; emit manifest."""
from pathlib import Path
import sys, json, argparse
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.prompt.builder import PromptBuilder

ap = argparse.ArgumentParser()
ap.add_argument("--transcript", type=str, default="")
ap.add_argument("--transcript-file", type=str, default=None)
ap.add_argument("--metadata", type=str, default="")
ap.add_argument("--latest-action", type=str, required=True)
ap.add_argument("--out", type=str, default="prompts/manifests/last_prompt.json")
ap.add_argument("--prompt-out", type=str, default=None)
args = ap.parse_args()

transcript = Path(args.transcript_file).read_text() if args.transcript_file else args.transcript
pb = PromptBuilder("prompts/original/policy.txt")
prompt, manifest = pb.build(transcript=transcript, metadata=args.metadata, latest_action=args.latest_action)
Path(args.out).write_text(json.dumps(manifest.to_dict(), indent=2))
if args.prompt_out:
    Path(args.prompt_out).write_text(prompt)
print(json.dumps(manifest.to_dict(), indent=2))
print(f"prompt chars={manifest.characters} policy_hash={manifest.policy_hash[:12]} prompt_hash={manifest.prompt_hash[:12]}")
