"""Build a standalone desktop executable on the target operating system."""

import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    system = platform.system()
    work = ROOT / "build"
    work.mkdir(exist_ok=True)
    env = {**os.environ, "PYINSTALLER_CONFIG_DIR": str(work / "cache")}
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--name",
        "PromVector Agent",
        "--specpath",
        str(work),
        "--exclude-module",
        "pytest",
    ]
    if system == "Darwin":
        args.extend(("--onedir", "--windowed", "--osx-bundle-identifier", "com.promvector.agent"))
    elif system == "Windows":
        args.extend(("--onefile", "--windowed"))
    elif system == "Linux":
        args.append("--onefile")
    else:
        raise SystemExit(f"Unsupported build platform: {system}")
    args.extend(("--hidden-import", "psycopg_binary", str(ROOT / "scripts" / "desktop_entry.py")))
    subprocess.run(args, cwd=ROOT, env=env, check=True)


if __name__ == "__main__":
    main()
