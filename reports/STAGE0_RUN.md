# Stage 0 — how to run the diagnostics

Two scripts. A+B+C need a GPU (CPU is ~100 s/sample at 8K). D is CPU-only by design.

## A/B/C on Colab T4 (~5 min)

```bash
!git clone --depth 1 https://github.com/keypaa/tiny-auto-classifier.git
%cd tiny-auto-classifier
!pip -q install "peft>=0.11" "onnxruntime>=1.18" --extra-index-url https://pypi.nvidia.com
!unzip -q pilot_t3_8192.zip -d .   # adapter, if you do not have it on this session
!python scripts/diag_stage0.py --adapter pilot_t3_8192 --limit 300
```

Reads it:
```python
import json
d = json.load(open("reports/stage0_diag.json"))
print("A conditions:", json.dumps(d["conditions"], indent=1))
print("B by category:", json.dumps(d["B_by_category"], indent=1))
print("B by suffix len:", json.dumps(d["B_by_suffix_tokens"]["buckets"], indent=1))
print("C confusion:", json.dumps(d["C_confusion_by_category"], indent=1))
print("C pair consistency:", d["C_pair_consistency"])
```

## D on the Ryzen (CPU), skip the 100 s torch baseline
```bash
python scripts/diag_onnx.py --skip-fp32-torch --iters 3
```

## What each result means

| Result | Conclusion | Action |
|---|---|---|
| A: `none` ~= `full` accuracy | policy is decorative, model memorized it | policy-faithfulness becomes the top priority |
| A: `none` much worse | policy IS being read | constraint is earning its keep |
| A: `compact` ~= `full` | 8K of policy is enough | shorter context would be fine |
| B: errors concentrated in `long_context` | length implicated | full 27K training justified |
| B: errors flat across categories | data implicated | rule engine before any new training |
| C: high always-BLOCK rate per category | prior collapse, not learning | needs more/better data |
| C: pair consistency < 0.8 | boundary not learned | minimal pairs are not doing their job |
| D: measured speedup < 2x | INT8 will not save an encoder at 27K | decoder + prefix cache is the only path |

## Known limitations of these diagnostics
- Our policy is synthetic (one repeated paragraph), so we cannot test policy *ordering*
  sensitivity or write meaningful perturbed policies. That test needs the real policy.
- `rule_id` is `R17` for every BLOCK sample: there is no rule taxonomy in the data, so
  rule-level confusion (roadmap section 36) is not measurable yet.
- `limit 300` gives a false-allow standard error of roughly +/-2 points. Enough to
  separate 0.88 from 0.99, not enough to certify 0.01.
