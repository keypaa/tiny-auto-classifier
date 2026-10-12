"""Fine-tune ProCreations encoders on Modal GPUs.

Thin launcher around scripts/train_unsloth.py (plain transformers+peft
path — no Unsloth dependency inside the container, so nothing can drift
from the validated recipe). Data pulls from HF; only scripts/ uploads.
Adapters persist on a Modal Volume; results download to reports/.

Usage:
    modal run modal_train.py --base ProCreations/auto-200m-2 \\
        --max-train 50000 --job-name m200-smoke
    modal run modal_train.py --base ProCreations/auto-200m-2 \\
        --max-train 0 --job-name m200-full --epochs 1
"""

import modal

IMAGE = (
    modal.Image.debian_slim(python_version="3.11")
    # Stack mirrors ProCreations' evidence (environment.json): transformers
    # 5.16.1 + kernels 0.16.2. The kernels lib must match transformers'
    # hub_kernels integration — latest kernels broke transformers 5.12.1 at
    # IMPORT time (LayerRepository revision error), poisoning even SDPA runs.
    # FA2 resolves through this pair; the train script still falls back to
    # SDPA with a log line if resolution fails at runtime.
    .pip_install("torch", "transformers==5.16.1", "peft", "datasets",
                 "tokenizers>=0.19", "scikit-learn", "kernels==0.16.2")
    .add_local_dir(
        "/home/keypaa/Projects/tiny-auto-classifier/scripts",
        remote_path="/app/scripts",
    )
)
APP = modal.App("tiny-auto-classifier-train")
VOL = modal.Volume.from_name("tiny-auto-models", create_if_missing=True)


@APP.function(image=IMAGE, gpu="L4", timeout=3600,
              volumes={"/models": VOL})
def train_job(base: str, max_train: int, job_name: str,
              epochs: float = 1.0, max_len: int = 8192,
              batch: int = 4, lr: float = 2e-4,
              max_val: int = 1000) -> dict:
    import subprocess
    import sys

    cmd = [sys.executable, "/app/scripts/train_unsloth.py",
           "--base", base, "--max-train", str(max_train),
           "--epochs", str(epochs), "--max-len", str(max_len),
           "--batch", str(batch), "--lr", str(lr),
           "--max-val", str(max_val),
           "--out", f"/models/{job_name}"]
    print(" ".join(cmd), flush=True)
    r = subprocess.run(cmd)
    VOL.commit()
    return {"job": job_name, "returncode": r.returncode}


@APP.function(image=IMAGE, gpu="L4", timeout=1200)
def profile_batches(base: str = "ProCreations/auto-200m-2") -> dict:
    """Thin wrapper: single implementation lives in scripts/profile_batches.py
    (also runnable directly on Molab/Colab/local)."""
    import sys

    sys.path.insert(0, "/app/scripts")
    from profile_batches import profile_batches as run

    return run(base)


@APP.local_entrypoint()
def main(base: str = "ProCreations/auto-200m-2", max_train: int = 50000,
         job_name: str = "m200-smoke", epochs: float = 1.0,
         max_val: int = 1000, profile: bool = False) -> None:
    import json

    if profile:
        print(json.dumps(profile_batches.remote(base), indent=2))
        return
    res = train_job.remote(base, max_train, job_name, epochs,
                           8192, 2, 2e-4, max_val)
    print(json.dumps(res, indent=2))
    if res["returncode"] != 0:
        raise SystemExit(f"training failed: {res}")
    print(f"adapter persisted on volume tiny-auto-models:/models/{job_name}")
    print("fetch with: modal volume get tiny-auto-models "
          f"/models/{job_name} models/checkpoints/{job_name}")


if __name__ == "__main__":
    raise SystemExit("run via: modal run modal_train.py -- ...")
