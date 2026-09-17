"""Startup hook for the deployed dashboard UI migration.

Python imports sitecustomize automatically before app.py. The hook applies the
idempotent dashboard patch whenever this repository is executed, unless explicitly
disabled with JSM_DISABLE_RUNTIME_UI_PATCH=1. This keeps the existing Render start
command compatible while the UI migration is folded back into index.html later.
"""

import os
from pathlib import Path


if os.environ.get('JSM_DISABLE_RUNTIME_UI_PATCH') != '1':
    root = Path(__file__).resolve().parent
    if (root / 'index.html').is_file() and (root / 'runtime_ui_patch.py').is_file():
        try:
            from runtime_ui_patch import main as apply_runtime_ui_patch

            apply_runtime_ui_patch()
        except Exception as exc:  # fail closed: do not silently serve a half-patched UI
            raise RuntimeError(f'JSM_UI_PATCH_FAILED: {exc}') from exc
