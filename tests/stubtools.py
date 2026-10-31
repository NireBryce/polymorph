"""A fake `gh` / `git` for tests: executables on a temp PATH that log their argv and
answer from a scenario. Nothing here talks to a network or a real repository."""
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

STUB_SOURCE = '''#!{python}
import json, os, sys
d = os.environ["STUB_DIR"]
name = os.path.basename(sys.argv[0])
argv = [name] + sys.argv[1:]
with open(os.path.join(d, "calls.log"), "a") as f:
    f.write(json.dumps(argv) + "\\n")
rules = json.load(open(os.path.join(d, "scenario.json")))
for i, rule in enumerate(rules):
    m = rule["match"]
    if argv[:len(m)] != m:
        continue
    seen_path = os.path.join(d, "seen-%d" % i)
    n = int(open(seen_path).read()) if os.path.exists(seen_path) else 0
    open(seen_path, "w").write(str(n + 1))
    outs = rule["outputs"]
    o = outs[min(n, len(outs) - 1)]
    sys.stdout.write(o.get("out", ""))
    sys.stderr.write(o.get("err", ""))
    sys.exit(o.get("rc", 0))
sys.stderr.write("stub: no rule for %r\\n" % (argv,))
sys.exit(97)
'''


class Stubs:
    """Create with a scenario: a list of {"match": [argv prefix], "outputs": [{rc,out,err}, ...]}.
    The n-th matching call gets outputs[n]; the last output repeats. First matching rule wins."""

    def __init__(self, scenario, names=("gh", "git")):
        self.dir = Path(tempfile.mkdtemp(prefix="pm-stub-"))
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        for n in names:
            p = self.bin / n
            p.write_text(STUB_SOURCE.format(python=sys.executable))
            p.chmod(p.stat().st_mode | stat.S_IXUSR)
        (self.dir / "scenario.json").write_text(json.dumps(scenario))

    def env(self):
        """PATH only (stubs first, then the system's), plus the stub's own state directory."""
        return {"PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}", "STUB_DIR": str(self.dir)}

    def calls(self):
        p = self.dir / "calls.log"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def out(rc=0, out="", err=""):
    return {"rc": rc, "out": out, "err": err}


def js(obj):
    return {"rc": 0, "out": json.dumps(obj)}
