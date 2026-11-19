"""Select independent projects for push, tag and manual events."""
import json, os, re, subprocess
from common import RELEASE_DIR, ROOT, projects

def select(event, ref, before, requested, changed=None):
    registry = projects()
    if event == "workflow_dispatch":
        if requested == "all": return list(registry)
        if requested not in registry: raise ValueError("Unknown project: " + requested)
        return [requested]
    if ref.startswith("refs/tags/"):
        tag = ref.removeprefix("refs/tags/")
        matches = [key for key in registry if re.fullmatch(re.escape(key) + r"-v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", tag)]
        if not matches: raise ValueError("Unsupported version tag: " + tag)
        return matches
    if changed is None:
        if not before or set(before) == {"0"}: return list(registry)
        # Do not silently release every project if Git history is unavailable.
        raw = subprocess.check_output(["git", "diff", "--name-only", "-z", before, "HEAD"], cwd=ROOT)
        changed = raw.decode("utf-8").split("\0")
    shared = (".github/workflows/", RELEASE_DIR.relative_to(ROOT).as_posix() + "/")
    if any(path.startswith(shared) for path in changed):
        return list(registry)
    return [key for key, cfg in registry.items() if any(p.startswith(cfg["directory"] + "/") for p in changed)]

if __name__ == "__main__":
    result = select(os.getenv("EVENT_NAME", "push"), os.getenv("GITHUB_REF", ""), os.getenv("BEFORE_SHA", ""), os.getenv("REQUESTED_PROJECT", "all"))
    print(json.dumps(result))
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
            out.write("projects=" + json.dumps(result, separators=(",", ":")) + "\n")
