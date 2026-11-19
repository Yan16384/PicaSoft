"""Verify release assets and tag commit after upload, without modifying remote state."""
import argparse, hashlib, json, os, subprocess
from pathlib import Path

def api(path):
    return json.loads(subprocess.check_output(["gh", "api", path], text=True, encoding="utf-8"))

def verify(tag, output):
    repository=os.environ["GH_REPO"]
    release=api("repos/"+repository+"/releases/tags/"+tag)
    if release["tag_name"]!=tag: raise RuntimeError("Release tag mismatch")
    commit=api("repos/"+repository+"/commits/"+tag)["sha"]
    if commit!=os.environ["GITHUB_SHA"]: raise RuntimeError("Tag does not point to the built source commit")
    assets={item["name"]:item for item in release["assets"]}
    files=[p for p in output.rglob("*") if p.is_file() and (p.suffix in {".exe", ".apk"} or p.name.endswith("-source.zip") or p.name=="SHA256SUMS.txt")]
    if not files: raise RuntimeError("No local release assets")
    for path in files:
        asset=assets.get(path.name)
        if not asset or asset["state"]!="uploaded" or asset["size"]!=path.stat().st_size: raise RuntimeError("Incomplete remote asset: "+path.name)
        digest=asset.get("digest")
        if digest and digest!="sha256:"+hashlib.sha256(path.read_bytes()).hexdigest(): raise RuntimeError("Remote checksum mismatch: "+path.name)
    print(json.dumps({"tag":tag,"commit":commit,"verified_assets":[p.name for p in files]}))

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--output",type=Path,required=True);a=p.parse_args();verify(os.environ["TAG"],a.output.resolve())
