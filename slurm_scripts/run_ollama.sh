#!/bin/bash
# TEMPLATE — edit the <PLACEHOLDER> values for your HPC account/paths/model
# before submitting. Starts an Ollama server on a GPU node, waits for it, then
# runs the local term extractor (scripts/run.py) against it.
#SBATCH -J f2w_ollama
#SBATCH -o out.f2w_ollama_%j
#SBATCH -e err.f2w_ollama_%j
#SBATCH -N 1
#SBATCH -C gpu
#SBATCH -G 1
#SBATCH --ntasks=1
#SBATCH --gpus-per-task=1
#SBATCH -t 0:10:00
#SBATCH -q debug
#SBATCH -A <ACCOUNT>
#SBATCH --mail-type=ALL
#SBATCH --mail-user=<EMAIL>

# <REPO_PATH>: absolute path to this repo on the HPC filesystem.
REPO_PATH=<REPO_PATH>

export PATH=$REPO_PATH/ollama/bin:$PATH
export OLLAMA_MODELS=$REPO_PATH/ollama/ollama_models
export OLLAMA_NUM_PARALLEL=2
ollama serve &
OLLAMA_PID=$!
until ollama list &>/dev/null; do sleep 1; done

cd $REPO_PATH
source $REPO_PATH/.venv/bin/activate
python scripts/run.py --backend "ollama" --model "qwen3.5:9b" --workers 2 --log-file f2w_ollama.log

kill $OLLAMA_PID
