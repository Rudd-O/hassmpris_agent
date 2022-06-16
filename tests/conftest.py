import sys
from os.path import dirname as d
from os.path import abspath, join, pathsep

root_dir = d(d(abspath(__file__)))
env = join(root_dir, ".env")
with open(env) as f:
    pythonpaths = [x[11:].rstrip() for x in f.readlines() if x.startswith("PYTHONPATH")]
    if pythonpaths:
        pythonpath = pythonpaths[0]
        pythonpath = pythonpath.replace("${env:PROJ_DIR}", root_dir)
        pythonpaths = pythonpath.split(pathsep)
        for p in pythonpaths:
            sys.path.append(p)
sys.path.append(join(root_dir, "src"))
