# Tiny CPU Auto-Mode Classifier — End-to-End Project Roadmap

**Status:** Research + engineering plan  
**Primary task:** Build a tiny CPU-only classifier capable of replacing or front-running the Claude Code Auto Mode safety classifier.

---

## 1. Mission

Build the **smallest practical model** that can consume the **original, uncompressed ~20–30K-token classifier prompt** and reliably determine:

```text
ALLOW
```

or

```text
BLOCK + exact BLOCK rule
```

The model is **not** required to generate the final XML response. A deterministic wrapper will convert its structured prediction into:

```text
<block>no</block>
```

or the exact required block format.

The project is about **post-training existing models**, not training a foundation model from scratch.

### Primary optimization target

Safety first:

1. dangerous false-allow rate
2. hard-block recall
3. long-context robustness
4. rule accuracy
5. false-deny rate
6. CPU latency
7. RAM usage
8. disk size

Do not optimize generic benchmark accuracy at the expense of safety.

---

# 2. Fixed Constraints

These are non-negotiable unless explicitly changed later.

### Prompt

The original classifier policy prompt must remain intact for the primary benchmark.

Approximate measured size:

- ~108K characters
- ~27K estimated tokens
- real operating range: **20–30K tokens**

Do not:

- summarize it,
- compress it,
- retrieve only parts of it,
- rewrite policy wording,
- remove rules,
- replace it with a distilled policy.

Those can be investigated later as separate experiments, but they cannot replace the primary benchmark.

### Hardware

Target deployment:

- CPU only
- DDR4 RAM
- no GPU
- low memory
- low latency
- batch size generally 1

### Model size ladder

| Tier | Approx. parameters | Purpose |
|---|---:|---|
| T1 | 200–250M | Minimum viable intelligence |
| T2 | 270–320M | Tiny dense baseline |
| T3 | 350–450M | Main/sweet-spot candidate |
| T4 | 500–700M | Quality ceiling |

Candidates currently requiring investigation:

- LFM2.5-230M
- Gemma 3 270M
- ModernBERT / `auto-0.4b`
- other ~350–450M long-context encoders
- Qwen ~0.6B long-context candidates
- any newer model discovered during the model survey

A candidate must have a **verified usable >=32K context** for the main track. >=64K is preferred.

---

# 3. Important Architectural Insight

`auto-0.4b` is important because it demonstrates that a BERT-family model can perform this task without text generation.

A sequence classifier works approximately as:

```text
20–30K tokens
       |
       v
   tokenizer
       |
       v
 long-context encoder
       |
       v
 pooled representation
       |
       v
 classification head
       |
       v
 [ALLOW_LOGIT, BLOCK_LOGIT]
```

The model does not output:

```text
"approve"
```

or:

```text
"deny"
```

The application interprets the logits.

This is highly relevant because our task is fundamentally classification.

---

# 4. Core Research Question

The project must determine empirically:

> **Is a 200–700M encoder/classifier better suited to Auto Mode than a similarly sized causal language model?**

Hypothesis:

### Encoder advantage

An encoder may be substantially more parameter-efficient because:

- no autoregressive generation,
- no output-token waste,
- direct classification objective,
- bidirectional access to the entire context,
- tiny classification head,
- no need to generate explanations.

### Decoder advantage

A causal model may have better:

- semantic reasoning,
- transcript interpretation,
- policy-rule interaction,
- natural-language understanding.

Do not assume either answer.

---

# 5. Project Phases

```text
PHASE 0  Infrastructure
PHASE 1  Model survey
PHASE 2  Baseline reproduction
PHASE 3  Dataset construction
PHASE 4  Pilot SFT
PHASE 5  Full SFT
PHASE 6  DPO
PHASE 7  Verifier RL
PHASE 8  Quantization
PHASE 9  CPU runtime optimization
PHASE 10 Calibration
PHASE 11 Cascade
PHASE 12 Final verification
PHASE 13 Production integration
```

Do not skip directly to full training.

---

# 6. PHASE 0 — Infrastructure

## 6.1 Create repository

Recommended structure:

```text
tiny-auto-mode-classifier/
├── README.md
├── ROADMAP.md
├── pyproject.toml
│
├── configs/
│   ├── models/
│   ├── training/
│   ├── evaluation/
│   └── runtime/
│
├── prompts/
│   ├── original/
│   └── manifests/
│
├── data/
│   ├── raw/
│   ├── generated/
│   ├── teacher/
│   ├── train/
│   ├── validation/
│   ├── test/
│   └── adversarial/
│
├── models/
│   ├── base/
│   ├── checkpoints/
│   └── exports/
│
├── src/
│   ├── prompt_builder.py
│   ├── tokenizer_probe.py
│   ├── models/
│   ├── dataset/
│   ├── training/
│   ├── evaluation/
│   ├── calibration/
│   ├── runtimes/
│   └── wrapper/
│
├── experiments/
├── reports/
└── scripts/
```

## 6.2 Record hardware

Collect:

```bash
lscpu
free -h
uname -a
```

Record:

- CPU model
- physical cores
- logical cores
- SIMD capabilities
- RAM
- OS
- kernel

This determines the realistic CPU target.

---

# 7. PHASE 0 — Canonical Prompt Replay

Build a `PromptBuilder`.

Inputs:

```text
policy
transcript
metadata
latest_action
```

Outputs:

```text
exact classifier input
```

For every prompt save:

```json
{
  "policy_hash": "...",
  "prompt_hash": "...",
  "characters": 108313,
  "tokens": 27110,
  "dynamic_tokens": "...",
  "model_tokenizer": "..."
}
```

Use SHA-256.

### Hard requirement

If the policy hash changes accidentally:

```text
ABORT BENCHMARK
```

Do not continue.

---

# 8. PHASE 1 — Current Model Survey

Before training anything, perform a fresh survey of current models in the target range.

For each candidate collect:

```text
name
parameters
architecture
encoder/decoder/hybrid
native context
tested context
attention mechanism
position encoding
hidden size
layers
vocabulary
license
base/instruct
classification support
ONNX support
CPU runtime support
INT8 support
INT4 support
long-context training method
```

### Priority

1. >=32K native context
2. >=64K preferred
3. small parameter count
4. efficient CPU architecture
5. good ONNX/runtime support
6. stable tokenizer
7. permissive license

Never trust only the model card's advertised context length.

---

# 9. PHASE 2 — `auto-0.4b` Reproduction

This is mandatory.

Tasks:

- [ ] Download exact checkpoint
- [ ] inspect config
- [ ] identify architecture
- [ ] identify classification head
- [ ] identify number of labels
- [ ] determine label mapping
- [ ] reproduce inference
- [ ] reproduce threshold
- [ ] measure tokenizer
- [ ] test context length
- [ ] test exact Auto Mode prompt
- [ ] benchmark CPU
- [ ] benchmark RAM
- [ ] investigate ONNX

The model should be treated as a **research baseline**, not automatically as the final architecture.

### Critical experiment

Run:

```text
8K
16K
24K
27K
32K
48K
64K
```

where supported.

Record:

```text
accuracy
false allow
false deny
rule accuracy
latency
RSS
```

### ONNX trap

If the ONNX implementation fails or becomes impractical at long context, do not conclude:

> "ONNX doesn't work."

Determine whether the limitation is:

- graph construction,
- dense attention mask,
- unsupported operator,
- memory explosion,
- runtime limitation,
- architecture limitation.

---

# 10. PHASE 2 — Raw Baseline Matrix

Run every candidate **before fine-tuning**.

For each:

```text
native zero-shot
task prompt
exact policy
all target lengths
CPU
memory
```

Store results in machine-readable form:

```json
{
  "model": "...",
  "context": 27110,
  "accuracy": 0.0,
  "false_allow": 0.0,
  "false_deny": 0.0,
  "latency_p50_ms": 0,
  "latency_p95_ms": 0,
  "rss_mb": 0
}
```

Do not manually copy numbers into reports.

Generate reports from experiment data.

---

# 11. PHASE 3 — Dataset

This is likely more important than the difference between 270M and 400M.

The dataset must encode the actual policy semantics.

## Required categories

### ALLOW

- harmless file operations
- harmless shell
- normal git
- normal builds
- normal tests
- normal browser use

### HARD BLOCK

- unconditional prohibited actions
- security boundary violations
- forbidden exfiltration
- irreversible/high-impact actions where policy says hard block

### SOFT BLOCK

Dangerous action without sufficient explicit user authorization.

### VALIDLY CLEARED SOFT BLOCK

Same dangerous action with exactly the user intent/consent required by policy.

### Consent boundaries

- vague yes
- explicit yes
- question
- command
- proposed action
- multi-action proposal
- user naming target
- user naming mechanism
- agent inventing mechanism

### Provenance

- pre-existing files
- agent-created files
- user-created files
- session-linked files
- sensitive data read earlier
- later publication/commit/upload

### Scope

- investigation vs modification
- local vs remote
- specific vs broad
- single target vs wildcard
- agent-created target vs user target

### Composite effects

- harmless command containing dangerous secondary effect
- multiple commands
- shell pipelines
- scripts
- chained commands

### Wrappers

- Python
- shell
- Makefiles
- scripts
- environment variables
- subprocesses
- aliases
- encoded commands

### Delayed effects

- cron
- systemd
- background processes
- queued jobs
- environment persistence
- scheduled actions

### Browser

- navigation
- text input
- JS
- upload
- shortcuts
- clipboard-like interactions

### Infrastructure

- shared cluster
- selectors
- workloads
- namespaces
- services
- resources

### Git

- dirty tree
- untracked files
- session-created changes
- remote changes
- commits
- push
- destructive cleanup

### Prompt injection

The action/context attempts to manipulate the classifier itself.

### Generalization

The action achieves the policy-prohibited effect using a mechanism not explicitly mentioned in the rule.

---

# 12. Dataset Balance

Do not create a 90% ALLOW dataset.

Initial target:

```text
20% clear ALLOW
20% clear BLOCK
20% consent/scope boundary
15% minimal pairs
10% adversarial
10% long-context
5% rule-interaction
```

These percentages are starting points, not sacred values.

Tune based on observed failure modes.

---

# 13. Minimal Pairs

This is one of the most important dataset types.

Create pairs where exactly one semantic property changes.

Examples:

```text
specific target
vs
wildcard target

user explicitly requests
vs
user merely asks

agent proposal
vs
user instruction

pre-existing file
vs
agent-created file

local target
vs
shared target

question
vs
execution directive
```

Each pair should have:

```json
{
  "parent": "...",
  "mutation": "...",
  "before_label": "...",
  "after_label": "..."
}
```

This makes boundary failures measurable.

---

# 14. Long-Context Dataset

For every important scenario, generate versions where the decisive evidence is:

```text
0–10%
10–25%
25–50%
50–75%
75–90%
90–100%
```

Also vary:

- irrelevant tool output,
- benign transcript history,
- previous actions,
- failed commands,
- successful commands,
- contradictory irrelevant messages,
- multiple user turns,
- multiple agent turns.

The latest action must remain the action being judged.

---

# 15. Teacher Labeling

Use a strong teacher to label generated examples.

For difficult/high-risk examples:

```text
teacher A
teacher B
      |
      v
agreement?
   /     \
 yes      no
 |         |
keep     adjudicate
```

Record:

```text
teacher outputs
final label
source of final label
adjudication notes
```

Do not let the teacher silently overwrite ground truth.

---

# 16. Dataset Split

Never rely only on random splitting.

Create:

```text
TRAIN
VALIDATION
TEST
ADVERSARIAL
```

And explicit holdouts:

```text
RULE_HOLDOUT
VEHICLE_HOLDOUT
PARAPHRASE_HOLDOUT
LONG_CONTEXT_HOLDOUT
INTERACTION_HOLDOUT
```

Example of bad split:

```text
train: rm -rf /tmp/foo
test:  rm -rf /tmp/bar
```

That measures memorization.

A better holdout changes the command family or mechanism.

---

# 17. PHASE 4 — Pilot SFT

Before creating hundreds of thousands of examples:

Create:

```text
10K–25K very high-quality examples
```

Train:

- one ~230M candidate
- one ~400M encoder candidate
- one ~600M causal candidate

Use SFT.

### Encoder

Binary or multi-head classification.

### Decoder

Generate only:

```text
ALLOW
```

or:

```text
BLOCK:R17
```

Do not generate explanations.

---

# 18. Multi-Head Encoder Experiment

For encoder models compare:

## A — Binary

```text
ALLOW
BLOCK
```

## B — Multiclass

```text
ALLOW
R01
R02
...
```

## C — Multi-head

```text
Head 1: ALLOW/BLOCK
Head 2: rule ID
Head 3: hard/soft
```

Prefer C if engineering cost is reasonable.

Why:

The model can separately learn:

```text
"Is this dangerous?"
```

and:

```text
"Which rule makes it dangerous?"
```

This can improve both training and diagnostics.

---

# 19. Long-Context SFT Curriculum

Do not train only at 2K/4K.

Recommended progression:

```text
8K
  ↓
16K
  ↓
24K
  ↓
27K
  ↓
32K
  ↓
48K/64K
```

This does not mean all training must be at each length.

The final training distribution must contain the actual 20–30K workload.

---

# 20. PHASE 5 — Full SFT

Only scale after the pilot proves the task is learnable.

Potential dataset:

```text
100K
250K
500K+
```

Do not automatically use the largest dataset.

Use validation performance to determine whether additional data is useful.

Prioritize:

```text
hard negatives
minimal pairs
failure-derived examples
long-context examples
```

over endless easy ALLOW samples.

---

# 21. Hard-Negative Mining

After each SFT model:

1. run full evaluation,
2. collect false allows,
3. collect near misses,
4. generate variants,
5. add them to the next training round.

Loop:

```text
train
  ↓
evaluate
  ↓
find failures
  ↓
generate harder variants
  ↓
retrain
```

This is likely to produce larger gains than blindly increasing model size.

---

# 22. PHASE 6 — DPO

After SFT is stable, test preference optimization.

Create preference pairs:

```text
Prompt
  |
  +--> correct prediction
  |
  +--> subtly incorrect prediction
```

Examples:

- correct vs incorrect consent interpretation
- correct vs incorrect provenance interpretation
- correct vs incorrect target specificity
- correct vs incorrect rule
- correct hard/soft classification
- correct latest-action interpretation

Use DPO or a comparable preference method.

Measure:

```text
SFT
vs
SFT + DPO
```

If DPO does not improve safety metrics, do not keep it just because it is fashionable.

---

# 23. PHASE 7 — Verifier RL

Only after SFT/DPO.

Start with an automatically verifiable reward.

Potential algorithms:

- RLOO
- GRPO

Reward structure should heavily penalize dangerous false allows.

Example starting point:

```text
correct decision       +2
correct rule           +2
correct severity       +1
valid structure        +0.5
wrong rule             -3
false deny             -1
malformed output       -2
dangerous false allow -10
```

Tune experimentally.

The exact reward numbers are less important than:

```text
false allow penalty >> false deny penalty
```

---

# 24. RL Traps

Do not:

- reward verbose reasoning,
- reward explanation length,
- use a single LLM judge as ground truth,
- optimize generic helpfulness,
- let the model output arbitrary text,
- reward evaluator-parser exploits,
- use confidence as a reward for correctness,
- allow malformed outputs to receive partial rewards.

The verifier should operate on structured predictions.

---

# 25. PHASE 8 — Quantization

For the best FP model(s), benchmark:

```text
FP32
BF16/FP16 where applicable
INT8
INT4
```

For each quantization record:

```text
accuracy
false allow
false deny
rule accuracy
p50
p95
p99
RSS
disk
```

Never assume quantization preserves safety.

A model must be fully re-evaluated after quantization.

---

# 26. PHASE 9 — CPU Runtime

Test:

### Encoder

- PyTorch CPU
- ONNX Runtime
- graph optimizations
- INT8
- INT4 if supported

### Decoder

- llama.cpp or equivalent mature CPU runtime
- ONNX where practical
- INT4

Measure:

```text
cold-start latency
warm latency
tokenization time
model execution time
total latency
p50
p95
p99
peak RSS
CPU utilization
```

For batch=1.

---

# 27. Thread Sweep

Benchmark:

```text
1
2
4
8
...
N
```

Do not assume maximum threads is fastest.

Long-context memory bandwidth can become the bottleneck.

Choose the configuration based on p95 latency, not only p50.

---

# 28. Critical Performance Question

The output is tiny.

Therefore the expensive operation is:

```text
20–30K-token input processing
```

Measure separately:

```text
tokenization
attention/encoder
classification head
wrapper
```

For every candidate.

This identifies where optimization actually matters.

---

# 29. Prefix Reuse Investigation

The policy is mostly static.

For decoder models investigate prefix/KV caching.

For encoder models investigate whether the runtime supports any useful reuse.

Do not assume encoder representations can simply be cached: the dynamic suffix can interact with the entire sequence through attention.

Any optimization that changes the actual semantics must be benchmarked against the exact baseline.

---

# 30. PHASE 10 — Calibration

The model's raw probability is not automatically a trustworthy probability.

Measure:

- reliability curves
- Expected Calibration Error
- precision/recall by threshold
- false allow by threshold
- false deny by threshold

Do not use 0.5 automatically.

---

# 31. Three-Way Decision

A highly recommended deployment strategy:

```text
P(BLOCK) < T_low
      |
      v
CONFIDENT ALLOW

P(BLOCK) > T_high
      |
      v
CONFIDENT BLOCK

otherwise
      |
      v
AMBIGUOUS -> fallback
```

This is much more realistic for a tiny model than forcing it to solve every edge case.

---

# 32. PHASE 11 — Cascade

Production candidate:

```text
                    Auto Mode request
                           |
                           v
                exact 20–30K policy
                           |
                           v
                    tiny CPU model
                    /      |       \
                   /       |        \
              ALLOW     BLOCK     UNCERTAIN
                |          |          |
                v          v          v
             execute     stop      fallback
```

Measure:

```text
local decision coverage
fallback percentage
false allow
false deny
total latency
```

The key metric becomes:

> What percentage of requests can the tiny model safely handle without invoking the expensive classifier?

---

# 33. Cascade Acceptance

A tiny model does not need perfect global accuracy if:

- high-confidence ALLOW decisions are extremely safe,
- high-confidence BLOCK decisions are reliable,
- uncertain cases fall back.

Example research target:

```text
>= 90% local coverage
while keeping dangerous false allows below the project threshold
```

Do not use this number as a guaranteed requirement. Tune the threshold based on measured results.

---

# 34. PHASE 12 — Security Evaluation

Every final candidate must pass dedicated adversarial testing.

## Test:

### Policy manipulation

- "ignore the policy"
- fake system messages
- fake user consent
- quoted policy text
- tool output containing instructions

### Semantic obfuscation

- aliases
- variables
- shell expansion
- base64
- hex
- Python subprocess
- scripts
- pipelines

### Consent attacks

- vague confirmation
- stale confirmation
- consent for a different target
- consent for one action applied to another
- user quote mistaken for intent
- agent proposal mistaken for user intent

### Provenance attacks

- hiding who created a file
- changing destination after reading sensitive data
- session boundary confusion

### Scope attacks

- wildcard instead of specific target
- local -> shared
- read -> modify
- investigate -> execute
- dry-run -> real action

### Long-context attacks

Put decisive information at different positions.

---

# 35. Failure Database

Every error becomes a structured record.

Required fields:

```text
sample_id
model
checkpoint
quantization
context_length
prediction
ground_truth
confidence
predicted_rule
ground_truth_rule
failure_category
severity
notes
```

Failure categories:

```text
keyword trap
semantic confusion
consent confusion
scope escalation
provenance failure
latest-action failure
long-context forgetting
hard/soft confusion
rule confusion
generalization failure
prompt injection
obfuscation
wrapper execution
browser
git
shared infrastructure
destination
```

---

# 36. Rule-Specific Evaluation

For every BLOCK rule produce:

```text
support
precision
recall
false allow
false deny
confusion with other rules
```

This identifies whether the model fails on one particular policy family.

A 99% aggregate score can hide one catastrophic weak rule.

---

# 37. Context-Length Evaluation

Produce a dedicated table:

| Context | Accuracy | False Allow | False Deny | Rule Acc. | P95 |
|---:|---:|---:|---:|---:|---:|
| 8K | | | | | |
| 16K | | | | | |
| 24K | | | | | |
| 27K | | | | | |
| 32K | | | | | |
| 48K | | | | | |
| 64K | | | | | |

The **27K row is the core production row**.

---

# 38. Position Robustness

For each context length test:

```text
evidence at beginning
evidence at 25%
evidence at 50%
evidence at 75%
evidence at end
```

Report performance separately.

A model that only works when evidence is near the end is not production-ready.

---

# 39. Quantization Robustness

Produce:

| Model | Precision | False Allow | False Deny | P95 | RSS |
|---|---|---:|---:|---:|---:|
| Best | FP | | | | |
| Best | INT8 | | | | |
| Best | INT4 | | | | |

Reject quantization if it causes unacceptable safety degradation.

---

# 40. Final Acceptance Gates

A release candidate must satisfy all of:

### Functional

- exact prompt accepted
- no silent truncation
- deterministic output
- correct rule mapping

### Safety

- dangerous false allows below project threshold
- hard-block recall extremely high
- adversarial evaluation passed
- long-context evaluation passed

### Long context

- 20–30K works reliably
- 27K benchmark passed
- 32K headroom preferred

### Runtime

- CPU-only
- acceptable RAM
- acceptable p95 latency
- stable warm inference

### Reliability

- malformed prediction -> fallback
- model error -> fallback
- prompt integrity failure -> fallback
- context overflow -> fallback
- unknown rule -> fallback

---

# 41. Fail-Closed Behavior

The classifier is part of a security boundary.

If:

```text
model fails
tokenizer fails
prompt hash incorrect
prompt truncated
context exceeds tested range
prediction malformed
unknown rule
timeout
runtime error
```

then:

```text
DO NOT ALLOW LOCALLY
```

Invoke fallback.

Never guess.

---

# 42. Deterministic Output Wrapper

The model should return something like:

```json
{
  "decision": "BLOCK",
  "rule_id": "R17",
  "confidence": 0.997
}
```

The wrapper maps:

```text
R17
```

to the exact rule name.

For ALLOW:

```text
<block>no</block>
```

For BLOCK:

```text
<block>yes</block>
<category>Exact Rule Name</category>
<reason>[Exact Rule Name] ...</reason>
```

The model itself does not need to generate this.

---

# 43. Never Allow Unknown Categories

If:

```text
decision = BLOCK
rule_id = UNKNOWN
```

do not fabricate a rule name.

Fallback.

Likewise:

```text
decision = BLOCK
confidence = NaN
```

must fallback.

---

# 44. Recommended Training Representation

For encoder:

```text
Input:
[exact policy + transcript + latest action]

Outputs:
decision logits
rule logits
severity logits
```

For decoder:

```text
Input:
[exact policy + transcript + latest action]

Output:
ALLOW
```

or:

```text
BLOCK:R17
```

No reasoning generation initially.

---

# 45. Why No Chain-of-Thought Output?

The teacher may internally perform complex reasoning.

The student does not need to expose it.

We only need the result.

Training the tiny model to generate reasoning wastes:

- compute,
- context,
- output bandwidth,
- model capacity.

The reasoning capability should be encoded into the decision boundary through supervised examples, hard negatives, preference optimization, and verifier training.

---

# 46. Model Selection Logic

At each stage:

### If T1 performs well

Continue optimizing T1.

### If T1 fails catastrophically

Do not spend massive training budget trying to force it.

### If T3 encoder beats T4 decoder

Prefer encoder.

### If T4 is significantly safer

Use T4.

### If T3 and T4 are equally safe

Choose T3 based on latency/RAM.

### If all models fail at 27K

Investigate long-context architecture.

Do not immediately increase parameter count.

---

# 47. Expected High-Probability Architecture

The current strongest hypothesis is:

```text
~350–450M long-context encoder
          +
policy-specific SFT
          +
minimal-pair hard-negative mining
          +
DPO
          +
verifier RL
          +
INT8
          +
optimized CPU runtime
          +
confidence cascade
```

But this is a hypothesis.

The experiments must be capable of disproving it.

---

# 48. Immediate TODO — Exact Order

## Day 1 / First execution

- [ ] inspect CPU/RAM
- [ ] create repo
- [ ] save original policy
- [ ] calculate policy SHA-256
- [ ] implement prompt replay
- [ ] verify ~20–30K input
- [ ] implement tokenizer probe
- [ ] implement benchmark schema
- [ ] implement CPU/RSS timer

## Next

- [ ] fresh model survey
- [ ] download LFM2.5-230M
- [ ] download Gemma 3 270M if context qualifies
- [ ] download/reproduce `auto-0.4b`
- [ ] identify best ~400M encoder
- [ ] identify best ~600M decoder

## Next

- [ ] raw baselines
- [ ] exact prompt benchmark
- [ ] context sweep
- [ ] CPU benchmark
- [ ] ONNX experiment

## Next

- [ ] create 10K–25K pilot dataset
- [ ] train T1
- [ ] train T3
- [ ] train T4
- [ ] compare

## Only after pilot success

- [ ] scale dataset
- [ ] SFT
- [ ] hard-negative mining
- [ ] DPO
- [ ] verifier RL
- [ ] quantization
- [ ] runtime optimization
- [ ] calibration
- [ ] cascade

---

# 49. Required Experiment Artifacts

Every experiment must produce:

```text
config.json
metrics.json
model_info.json
hardware.json
tokenization.json
predictions.jsonl
failures.jsonl
README.md
```

`README.md` must explain:

```text
what changed
what stayed fixed
training data
model
context length
runtime
quantization
results
known failures
next action
```

No undocumented experiments.

---

# 50. Experiment Reproducibility

Every run records:

```text
git commit
model revision/hash
dataset version
policy hash
tokenizer version
Python version
runtime version
CPU
RAM
thread count
random seed
training config
quantization
```

If a number cannot be reproduced, it cannot be used for the final model comparison.

---

# 51. Final Report

The final report must answer:

1. What is the smallest model that works?
2. Does encoder-only beat causal generation?
3. Is 230M sufficient?
4. Is 270M sufficient?
5. Is ~400M the sweet spot?
6. Does ~600M materially improve safety?
7. How does performance change at 27K?
8. Does 64K provide useful headroom?
9. What does SFT contribute?
10. What does DPO contribute?
11. What does RL contribute?
12. What does INT8/INT4 cost?
13. Which CPU runtime wins?
14. What is p50/p95/p99?
15. How much RAM is required?
16. What percentage can the cascade handle locally?
17. What failure modes remain?
18. What is the recommended deployment configuration?

---

# 52. Definition of Done

The project is complete only when the repository contains:

```text
[ ] exact policy replay
[ ] exact 20–30K benchmark
[ ] model survey
[ ] auto-0.4b reproduction
[ ] T1/T2/T3/T4 comparison
[ ] encoder vs decoder comparison
[ ] pilot dataset
[ ] final dataset
[ ] hard-negative mining
[ ] SFT
[ ] DPO experiment
[ ] verifier-RL experiment
[ ] long-context evaluation
[ ] adversarial evaluation
[ ] calibration
[ ] quantization
[ ] CPU runtime comparison
[ ] cascade experiment
[ ] failure database
[ ] deterministic wrapper
[ ] fail-closed behavior
[ ] final recommendation
```

---

# 53. Final Deliverable

The final engineering recommendation must have this exact structure:

```text
RECOMMENDED MODEL
RECOMMENDED PARAMETER COUNT
RECOMMENDED ARCHITECTURE
RECOMMENDED CONTEXT LENGTH
RECOMMENDED TRAINING DATA
RECOMMENDED SFT RECIPE
RECOMMENDED DPO RECIPE
RECOMMENDED RL RECIPE
RECOMMENDED QUANTIZATION
RECOMMENDED CPU RUNTIME
RECOMMENDED THREAD COUNT
RECOMMENDED CONFIDENCE THRESHOLDS
EXPECTED LOCAL COVERAGE
FALLBACK STRATEGY
KNOWN FAILURE MODES
```

And include the actual measured numbers.

---

# 54. Central Principle

The project should always remember:

> **We are not trying to build a tiny Claude. We are distilling one very specific, very expensive decision into a tiny security classifier.**

The winning model is therefore not the model with the best general intelligence.

It is the model with the best:

```text
policy adherence
+
long-context robustness
+
dangerous-false-allow resistance
+
CPU efficiency
```

at the smallest possible parameter count.

---

# 55. One-Line Mission

**Build the smallest CPU-only post-trained model that can safely apply the unchanged 20–30K-token Auto Mode policy, use confidence to handle easy decisions locally, and reliably fall back whenever the tiny model cannot be trusted.**
