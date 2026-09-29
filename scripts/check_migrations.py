"""Pre-commit hook: `makemigrations --check` under the project's own interpreter.

Git runs hooks with whatever `python` is first on PATH, which from an IDE or an
unactivated shell is a system Python with no Django. So look for the
virtualenv docs/development.md creates, then an active one, and fail with a
message rather than an ImportError when there is neither.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def project_python():
    venvs = [ROOT / '.venv']
    if os.environ.get('VIRTUAL_ENV'):
        venvs.append(Path(os.environ['VIRTUAL_ENV']))
    for venv in venvs:
        for candidate in (venv / 'Scripts' / 'python.exe', venv / 'bin' / 'python'):
            if candidate.exists():
                return str(candidate)
    return None


def main():
    python = project_python()
    if python is None:
        print('No virtualenv found at .venv and none active; cannot run makemigrations --check.')
        return 1
    return subprocess.call([python, 'manage.py', 'makemigrations', '--check', '--dry-run'], cwd=ROOT)


if __name__ == '__main__':
    sys.exit(main())
