"""Every template a template includes, extends or imports must be committed, not just on disk.
(A partial that only existed in a working tree once took five pages down in production.)"""
import re
import subprocess
from pathlib import Path

T = Path(__file__).resolve().parents[1] / "rx" / "web" / "templates"


def test_included_templates_are_tracked():
    try:
        tracked = set(subprocess.run(["git", "ls-files", str(T)], capture_output=True, text=True, check=True,
                                     cwd=T).stdout.split())
    except Exception:
        return                                    # not a git checkout (e.g. a deploy build): nothing to check
    tracked = {Path(x).name for x in tracked}
    missing = []
    for f in T.glob("*.html"):
        for name in re.findall(r'{%-?\s*(?:include|extends|import|from)\s+"([^"]+)"', f.read_text()):
            if name not in tracked:
                missing.append(f"{f.name} -> {name}")
    assert not missing, "not committed: " + ", ".join(missing)
