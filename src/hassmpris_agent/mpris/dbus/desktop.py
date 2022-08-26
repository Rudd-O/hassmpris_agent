from xdg.DesktopEntry import DesktopEntry


from xdg.BaseDirectory import xdg_data_dirs

from typing import Optional, Tuple
import os
import logging
from shlex import quote

_LOGGER = logging.getLogger(__name__)


def desktop_entry(name: str) -> Optional[Tuple[DesktopEntry, str]]:
    name = "%s.desktop" % name
    for d in xdg_data_dirs:
        for base, _, files in os.walk(os.path.join(d, "applications")):
            if name in files:
                path = os.path.join(base, name)
                try:
                    return DesktopEntry(path), path
                except Exception as e:
                    _LOGGER.warning(
                        "Ignoring desktop entry %s since it is invalid: %s",
                        path,
                        e,
                    )
    return None


def exec_from_desktop_entry(name: str) -> Optional[str]:
    f = desktop_entry(name)
    if not f:
        return None
    entry, path = f
    exe: str = entry.getExec() or ""
    if not exe:
        return None
    replacements = {
        "%f": "",
        "%F": "",
        "%u": "",
        "%U": "",
        "%i": "--icon %s" % quote(entry.getIcon()) if entry.getIcon() else "",
        "%c": quote(entry.getName()) if entry.getName() else "",
        "%k": quote(path),
    }
    for k, v in replacements.items():
        exe = exe.replace(k, v)
    return exe


if __name__ == "__main__":
    import sys

    print(exec_from_desktop_entry(sys.argv[1]))
