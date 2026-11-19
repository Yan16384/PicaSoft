"""Make a project-only complete source ZIP, checksums and release notes."""
import argparse, hashlib, json, os, re, subprocess, zipfile
from pathlib import Path
from common import RELEASE_DIR, ROOT, project

def source_files(key):
    cfg = project(key)
    paths = [
        cfg["directory"], RELEASE_DIR.relative_to(ROOT).as_posix(), ".github/workflows",
        "README.md", ".github/RELEASING.md", ".github/RELEASE-VALIDATION.md",
        "LICENSE*", "NOTICE*", ".gitignore",
    ]
    names = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", *paths], cwd=ROOT).decode("utf-8").split("\0")
    for name in sorted(set(names)):
        p = ROOT / name
        if not name or not p.is_file(): continue
        parts = Path(name).parts
        if any(part in {"build", "dist", ".gradle", ".venv", "__pycache__", ".release-output"} for part in parts): continue
        if p.suffix.lower() in {".exe", ".apk", ".zip", ".pyc", ".jks", ".keystore", ".p12"}: continue
        if p.name in {"local.properties", "album_config.json", "settings.json"} or p.name.startswith(".env"): continue
        yield p, name

def package(key, output):
    cfg = project(key); output.mkdir(parents=True, exist_ok=True)
    archive = output / (cfg["artifact"] + "-source.zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for path, name in source_files(key): z.write(path, name)
    with zipfile.ZipFile(archive) as z:
        required = cfg["directory"] + "/" + cfg["entry"]
        icon = str(Path(required).with_name("pica-icon.png").as_posix())
        if not {required, cfg["requirements"], icon}.issubset(z.namelist()):
            raise RuntimeError("Incomplete source archive")
    binaries = sorted(p for folder in (output / "windows", output / "android") if folder.is_dir() for p in folder.iterdir() if p.is_file() and p.suffix.lower() in {".exe", ".apk"})
    if not any(p.suffix == ".exe" for p in binaries): raise RuntimeError("Windows executable is missing")
    if cfg.get("android") and not any(p.suffix == ".apk" for p in binaries): raise RuntimeError("Android APK is missing; no partial release")
    files = [archive, *binaries]
    checksums = output / "SHA256SUMS.txt"
    checksums.write_text("".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.relative_to(output).as_posix() + "\n" for p in files), encoding="utf-8")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    notes = ["# " + cfg["artifact"], "", "Source commit: `" + commit + "`", "", "Independent application; includes Windows EXE, complete buildable source and SHA-256 checksums."]
    run_id = os.getenv("GITHUB_RUN_ID")
    if os.getenv("GITHUB_ACTIONS") == "true" and run_id:
        run_url = f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{run_id}"
        notes += ["", f"Built from source by [GitHub Actions]({run_url})."]
    if cfg.get("android"):
        notes += ["", f"Android package/version is defined in `{cfg['android']}/app/build.gradle`."]
        if any("-debug.apk" in p.name for p in binaries):
            notes += ["", "**Android debug test APK:** release signing is not configured. Hosted runners generate temporary debug keys; future APKs may not update an existing installation. Use your established release keystore for upgrade-compatible packages. Do not uninstall an existing app without backing up its data."]
        else: notes += ["", "Android release APK signed with the configured keystore. Preserve this keystore for future updates."]
    (output / "release-notes.md").write_text("\n".join(notes) + "\n", encoding="utf-8")
    ref = os.getenv("GITHUB_REF", "")
    tag = ref.removeprefix("refs/tags/") if ref.startswith("refs/tags/") else key + "-build-" + os.environ.get("GITHUB_RUN_NUMBER", "local")
    if not re.fullmatch(re.escape(key) + r"-(?:v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?|build-[0-9]+|build-local)", tag): raise ValueError("Invalid release tag")
    stable = ref.startswith("refs/tags/") and not re.search(r"[+-]", tag[len(key)+2:])
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f: f.write("tag=" + tag + "\nstable=" + str(stable).lower() + "\n")
    print(json.dumps({"tag": tag, "files": [str(p) for p in files]}, ensure_ascii=False))

if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("project"); p.add_argument("--output", required=True, type=Path); a = p.parse_args(); package(a.project, a.output.resolve())
