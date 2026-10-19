## Python Environment

- This project uses the existing Conda environment named `py3.11`.
- Run Python commands with `conda run -n py3.11 ...` or activate it with `conda activate py3.11`.
- Do not create `venv`, `.venv`, or temporary project Python environments.
- Do not use the system Python for installation, testing, or running Flowinone.
- Install project Python dependencies into `py3.11`.

## UI Scope

- Flowinone currently targets desktop browsing only.
- Design and verify against desktop windows (1280×900 and 1440×900), using mouse and keyboard.
- Do not add mobile layouts, phone breakpoints, touch-specific requirements, or mobile acceptance tests unless the user explicitly reintroduces that scope.
