import sys
from os.path import dirname as d
from os.path import abspath, join, pathsep

root_dir = d(d(abspath(__file__)))
env = join(root_dir, ".env")
try:
    with open(env) as f:

        def ispypath(s: str) -> bool:
            return s.startswith("PYTHONPATH")

        pythonpaths = [x[11:].rstrip() for x in f.readlines() if ispypath(x)]
        if pythonpaths:
            pythonpath = pythonpaths[0]
            pythonpath = pythonpath.replace("${env:PROJ_DIR}", root_dir)
            pythonpaths = pythonpath.split(pathsep)
            for p in pythonpaths:
                sys.path.append(p)
except FileNotFoundError:
    # The env file is not to be found, this is a production build.
    pass
sys.path.append(join(root_dir, "src"))
