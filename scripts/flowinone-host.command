#!/bin/zsh
# Finder/Terminal entry point; keep one foreground process in Conda py3.11.
set -u
cd -- "${0:A:h:h}" || exit 1

conda_path="${CONDA_EXE:-}"
if [[ ! -x "$conda_path" ]]; then
    conda_path="$(command -v conda 2>/dev/null)"
fi
if [[ ! -x "$conda_path" ]]; then
    for candidate in "$HOME/anaconda3/bin/conda" "$HOME/miniconda3/bin/conda" \
        "$HOME/miniforge3/bin/conda" /opt/anaconda3/bin/conda \
        /opt/miniconda3/bin/conda /opt/homebrew/bin/conda; do
        if [[ -x "$candidate" ]]; then
            conda_path="$candidate"
            break
        fi
    done
fi

if [[ ! -x "$conda_path" ]]; then
    print -u2 "找不到 Conda。請確認已安裝 Conda 與 py3.11 環境。"
    read -r "?按 Enter 關閉。"
    exit 1
fi

# Activate before exec so no Conda wrapper can outlive the terminal supervisor.
conda_base_path="$("$conda_path" info --base)" || exit 1
source "$conda_base_path/etc/profile.d/conda.sh" || exit 1
if ! conda activate py3.11; then
    print -u2 "無法啟用既有 Conda py3.11 環境。"
    exit 1
fi
exec python -u scripts/flowinone_host.py "$@"
