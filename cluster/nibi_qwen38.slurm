#!/bin/bash
#SBATCH --account=def-jic823_gpu
#SBATCH --job-name=qwen38-extract
#SBATCH --gres=gpu:nvidia_h100_80gb_hbm3_3g.40gb:1
#SBATCH --cpus-per-task=12
#SBATCH --mem=64G
#SBATCH --time=12:00:00
#SBATCH --output=%x-%j.out
#
# Nibi twin of cluster/plato_qwen38.slurm: serve Qwen3.8-27B-FP8 with vLLM
# on one H100 3g.40gb MIG slice (native FP8 on Hopper) and run an extraction
# task from the Canada50 repo. Same env interface:
#   TASK=schedule|census  EDITIONS="1897 1898 ..."  [CHUNKS=n] [FORCE=1]
#   [N_WINDOWS=k] [PARALLEL=3] [MAX_SEQS=4] [EFFORT=medium] [MAXLEN=40960]
# Fan-out: one job per volume list; results land in eval/results/<task><year>_…
# and are rsync'd back to WSL for remerge/link/adjudicate.
#
# Staging (once, login node): cluster/nibi_setup.sh. Submit ONLY when the
# OCR array is not using the MIG slices.

set -euo pipefail
C50=${C50:-$HOME/projects/def-jic823/canada50}
REPO=${REPO:-$C50/Canada50}
MODEL_DIR=${MODEL_DIR:-$C50/models/Qwen3.8-27B-FP8}
SIF=${SIF:-$C50/containers/vllm.sif}
MODEL_NAME=${MODEL_NAME:-Qwen/Qwen3.8-27B-FP8}
MAXLEN=${MAXLEN:-40960}
EFFORT=${EFFORT:-medium}
PORT=${PORT:-$((8000 + SLURM_JOB_ID % 1000))}   # several jobs may share a node
OUT=${OUT:-$REPO/eval/results/sched1902_qwen38_${EFFORT}}

module load apptainer 2>/dev/null || module load apptainer/1.3.5 2>/dev/null || true
CTR=$(command -v apptainer)
echo "node $(hostname)  job $SLURM_JOB_ID  $(date)"
nvidia-smi -L || true

mkdir -p "$OUT"
export APPTAINERENV_HF_HUB_OFFLINE=1 APPTAINERENV_HF_HOME=$C50/hf_cache APPTAINERENV_VLLM_LOGGING_LEVEL=INFO
RUN=("$CTR" exec --nv --bind "$C50":"$C50" --bind "$REPO":"$REPO" "$SIF" python3)
CLIENT_PY=("$CTR" exec --bind "$REPO":"$REPO" "$SIF" python3)

# ---- server (background)
"${RUN[@]}" -m vllm.entrypoints.openai.api_server \
    --model "$MODEL_DIR" --served-model-name "$MODEL_NAME" \
    --port "$PORT" --host 127.0.0.1 \
    --max-model-len "$MAXLEN" --gpu-memory-utilization "${GPU_UTIL:-0.93}" \
    --max-num-seqs "${MAX_SEQS:-4}" \
    --limit-mm-per-prompt '{"image":0,"video":0}' \
    ${EAGER:+--enforce-eager} \
    --enable-auto-tool-choice --tool-call-parser qwen3_coder \
    --reasoning-parser qwen3 \
    --trust-remote-code \
    > "$OUT/vllm-server-$SLURM_JOB_ID.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT

for i in $(seq 1 240); do
  if curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then echo "server up after ${i}x5s"; break; fi
  if ! kill -0 $SERVER 2>/dev/null; then echo "server died — see $OUT/vllm-server-$SLURM_JOB_ID.log"; exit 1; fi
  sleep 5
done
curl -s "http://127.0.0.1:$PORT/v1/chat/completions" -H 'Content-Type: application/json' -d "{
  \"model\": \"$MODEL_NAME\", \"max_tokens\": 64, \"chat_template_kwargs\": {\"enable_thinking\": false},
  \"messages\": [{\"role\": \"user\", \"content\": \"Reply with the single word: ready\"}]}" | head -c 300; echo

# ---- extraction
if [ -n "${EDITIONS:-}" ]; then
  TASK=${TASK:-schedule}
  case "$TASK" in census) CDIR=census_tables; PFX=census;; school) CDIR=school_tables; PFX=school;; officers) CDIR=officers_tables; PFX=officers;; table) CDIR=tabstmt_tables; PFX=tabstmt;; agstat) CDIR=agstat_tables; PFX=agstat; QTASK=table;; agrep) CDIR=agreport_tables; PFX=agrep; QTASK=table;; *) CDIR=reserves_schedules; PFX=sched;; esac
  for Y in $EDITIONS; do
    echo "==== $TASK $Y $(date)"
    "${CLIENT_PY[@]}" "$REPO/eval/extract_1902.py" --base-url "http://127.0.0.1:$PORT/v1" \
      --model "$MODEL_NAME" --reasoning-effort "$EFFORT" --edition "$Y" --task "${QTASK:-$TASK}" \
      --chunks-dir "$REPO/registries/external/$CDIR/chunks_$Y" \
      --out "$REPO/eval/results/${PFX}${Y}_qwen38_${EFFORT}" \
      --parallel "${PARALLEL:-3}" ${CHUNKS:+--chunks "$CHUNKS"} ${FORCE:+--force} ${N_WINDOWS:+--n-windows "$N_WINDOWS"}
  done
else
  "${CLIENT_PY[@]}" "$REPO/eval/extract_1902.py" --base-url "http://127.0.0.1:$PORT/v1" \
    --model "$MODEL_NAME" --out "$OUT" --reasoning-effort "$EFFORT" --parallel "${PARALLEL:-1}" \
    ${CHUNKS:+--chunks "$CHUNKS"} ${FORCE:+--force} ${N_WINDOWS:+--n-windows "$N_WINDOWS"}
fi
echo "extraction done $(date)"
