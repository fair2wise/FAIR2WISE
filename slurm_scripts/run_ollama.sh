#!/bin/bash
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
#SBATCH -A amsc006_g 
#SBATCH --mail-type=ALL
#SBATCH --mail-user=bowenzheng@lbl.gov

export PATH=/pscratch/sd/b/bzheng2/FAIR2WISE/ollama/bin:$PATH
export OLLAMA_MODELS=/pscratch/sd/b/bzheng2/FAIR2WISE/ollama/ollama_models
export OLLAMA_NUM_PARALLEL=2
ollama serve &
OLLAMA_PID=$!
until ollama list &>/dev/null; do sleep 1; done

cd /pscratch/sd/b/bzheng2/FAIR2WISE
source /pscratch/sd/b/bzheng2/FAIR2WISE/.venv/bin/activate
python run.py --backend "ollama" --model "qwen3.5:9b" --workers 2 --log-file f2w_ollama.log

kill $OLLAMA_PID