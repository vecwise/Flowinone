#!/bin/zsh
# Shared Finder/Terminal entry point; always use the project's Conda environment.
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

"$conda_path" run --no-capture-output -n py3.11 python scripts/flowinone_host.py "$@"
result=$?
if (( result != 0 )) && [[ -t 0 ]]; then
    read -r "?按 Enter 關閉。"
fi
exit "$result"
