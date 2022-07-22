import os
import shlex
import sys

import xdg.BaseDirectory


def folder() -> str:
    return os.path.join(
        xdg.BaseDirectory.xdg_config_home,
        "hassmpris",
    )


def program() -> list[str]:
    if os.path.basename(sys.argv[0]).endswith(".py"):
        return [
            sys.executable,
            os.path.join(
                os.path.dirname(__file__),
                "server.py",
            ),
        ]
    else:
        return [
            os.path.join(
                os.path.dirname(sys.argv[0]),
                "hassmpris-agent",
            )
        ]


def setup_autostart() -> None:
    exec_ = " ".join(shlex.quote(x) for x in program())
    text = f"""[Desktop Entry]
Exec={exec_}
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
