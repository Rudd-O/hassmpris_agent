import os
import shlex
import sys

import xdg.BaseDirectory


def folder() -> str:
    return os.path.join(
        xdg.BaseDirectory.xdg_config_home,
        "hassmpris",
    )


def program() -> str:
    return os.path.join(
        os.path.dirname(__file__),
        "server.py",
    )


def setup_autostart() -> None:
    executable = shlex.quote(sys.executable)
    path = shlex.quote(program())
    text = f"""[Desktop Entry]
Exec={executable} {path}
Type=Application
Terminal=false
"""
    folder = os.path.join(xdg.BaseDirectory.xdg_config_home, "autostart")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "hassmpris-autostart.desktop"), "w") as f:
        f.write(text)


def disable_autostart() -> None:
    folder = os.path.join(xdg.BaseDirectory.xdg_config_home, "autostart")
    f = os.path.join(folder, "hassmpris-autostart.desktop")
    try:
        os.unlink(f)
    except FileNotFoundError:
        pass
