#!/bin/bash
# One-time setup on the plato LOGIN node for cluster/plato_qwen38.slurm.
# Run:  bash cluster/plato_setup.sh        (idempotent; re-run to resume downloads)
#
# Creates under $C50 (default ~/projects/canada50):
#   containers/vllm.sif      singularity build of docker://vllm/vllm-openai:<tag>
#   models/Qwen3.8-27B-FP8   Hugging Face snapshot (official FP8 checkpoint, ~29 GB)
#   venv/                    tiny venv with huggingface_hub for the download
set -euo pipefail
C50=${C50:-$HOME/projects/canada50}
VLLM_TAG=${VLLM_TAG:-latest}
mkdir -p "$C50/containers" "$C50/models" "$C50/hf_cache"

module load apptainer/1.4.5 2>/dev/null || module load apptainer 2>/dev/null || module load singularity 2>/dev/null || true
CTR=$(command -v apptainer || command -v singularity)
export SINGULARITY_CACHEDIR=${SINGULARITY_CACHEDIR:-$C50/singularity_cache}
export SINGULARITY_TMPDIR=${SINGULARITY_TMPDIR:-$C50/singularity_tmp}
mkdir -p "$SINGULARITY_CACHEDIR" "$SINGULARITY_TMPDIR"

if [ ! -f "$C50/containers/vllm.sif" ]; then
  echo "== pulling vllm/vllm-openai:$VLLM_TAG (≈10 GB, takes a while)"
  $CTR pull "$C50/containers/vllm.sif" "docker://vllm/vllm-openai:$VLLM_TAG"
else
  echo "== container present: $C50/containers/vllm.sif"
fi

if [ ! -d "$C50/venv" ]; then
  python3 -m venv "$C50/venv"
  "$C50/venv/bin/pip" -q install --upgrade pip "huggingface_hub[cli]"
fi
export HF_HOME="$C50/hf_cache"
echo "== downloading Qwen/Qwen3.8-27B-FP8 → $C50/models/Qwen3.8-27B-FP8 (resumable)"
"$C50/venv/bin/hf" download Qwen/Qwen3.8-27B-FP8 --local-dir "$C50/models/Qwen3.8-27B-FP8" \
  || "$C50/venv/bin/huggingface-cli" download Qwen/Qwen3.8-27B-FP8 --local-dir "$C50/models/Qwen3.8-27B-FP8"
du -sh "$C50/models/Qwen3.8-27B-FP8"

if [ ! -d "$C50/Canada50" ]; then
  echo "== clone the repo into $C50/Canada50 (git clone <your remote> $C50/Canada50), or rsync it from WSL"
fi
echo "== setup done. Submit with: cd $C50/Canada50 && sbatch cluster/plato_qwen38.slurm"
