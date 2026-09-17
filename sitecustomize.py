"""Render startup hook for the deployed dashboard.

Python imports sitecustomize automatically during interpreter startup.  We use the
hook only on Render so the existing service can apply small UI migrations before
app.py reads index.html, without changing the service's manually configured start
command.
"""

import os


if os.environ.get('RENDER') == 'true' or os.environ.get('RENDER_SERVICE_ID'):
    try:
        from runtime_ui_patch import main as apply_runtime_ui_patch

        apply_runtime_ui_patch()
    except Exception as exc:  # fail closed: a broken UI patch must not silently ship
        raise RuntimeError(f'RENDER_UI_PATCH_FAILED: {exc}') from exc
