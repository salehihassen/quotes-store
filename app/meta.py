"""Version strings shown in the footer.

Neither number is written down in the source. The app version comes from git —
captured at build time into APP_VERSION, since the image ships no .git — and
the database version is asked of the server itself (see db.server_version).
Both are resolved once and cached; rendering a page never pays for them.
"""

import os
import subprocess
from functools import lru_cache
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def app_version() -> str:
    """Whatever git calls this checkout: a tag if one exists, else the commit.

    In the container APP_VERSION is baked in by the build. Running straight
    from the working tree there is no such variable, so ask git directly.
    """
    baked = os.getenv("APP_VERSION", "").strip()
    if baked:
        return baked
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=_REPO,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass  # no git, no checkout, or it hung -- the footer can live without it
    return "dev"
