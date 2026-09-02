#!/bin/bash
# One-time staging on the Nibi LOGIN node for cluster/nibi_qwen38.slurm.
# Stages only — submits nothing. Idempotent; re-run to resume.
#
#   nohup bash cluster/nibi_setup.sh > ~/projects/def-jic823/canada50/logs/setup.log 2>&1 &
#
# Creates under $C50 (default ~/projects/def-jic823/canada50, i.e. /project/6080182/canada50):
#   containers/vllm.sif        apptainer build of docker://vllm/vllm-openai:latest (≈8 GB)
#   models/Qwen3.8-27B-FP8     Hugging Face snapshot (official FP8, ≈29 GB)
#   venv/                      huggingface_hub for the download
set -uo pipefail
C50=${C50:-$HOME/projects/def-jic823/canada50}
VLLM_TAG=${VLLM_TAG:-latest}
mkdir -p "$C50"/{containers,models,hf_cache,apptainer_cache,apptainer_tmp,logs}
cd "$C50"

module load apptainer 2>/dev/null || module load apptainer/1.3.5 2>/dev/null || true
export APPTAINER_CACHEDIR=$C50/apptainer_cache APPTAINER_TMPDIR=$C50/apptainer_tmp
export HF_HOME=$C50/hf_cache

# --- model weights (background; resumable)
if [ ! -f "$C50/models/Qwen3.8-27B-FP8/config.json" ]; then
  [ -d venv ] || { python3 -m venv venv && venv/bin/pip -q install --upgrade pip huggingface_hub; }
  echo "== $(date) downloading Qwen/Qwen3.8-27B-FP8"
  venv/bin/hf download Qwen/Qwen3.8-27B-FP8 --local-dir models/Qwen3.8-27B-FP8 > logs/download.log 2>&1 &
  DL=$!
else
  echo "== weights present"; DL=""
fi

# --- container (foreground; the long step)
if [ ! -s containers/vllm.sif ]; then
  echo "== $(date) pulling vllm/vllm-openai:$VLLM_TAG"
  apptainer pull -F containers/vllm.sif "docker://vllm/vllm-openai:$VLLM_TAG" > logs/pull.log 2>&1 \
    && echo "== $(date) container built: $(ls -la containers/vllm.sif)" \
    || echo "== $(date) PULL FAILED — see logs/pull.log"
  rm -rf "$C50"/apptainer_tmp/*
else
  echo "== container present: $(ls -la containers/vllm.sif)"
fi

[ -n "${DL:-}" ] && wait $DL && echo "== $(date) weights: $(du -sh models/Qwen3.8-27B-FP8 | cut -f1)"
apptainer exec containers/vllm.sif python3 -c "import vllm; print('vllm', vllm.__version__)" 2>/dev/null | tail -1
echo "== $(date) setup done. Do NOT submit until the OCR array is finished; then:"
echo "   cd $C50/Canada50 && TASK=census EDITIONS=\"...\" sbatch cluster/nibi_qwen38.slurm"
