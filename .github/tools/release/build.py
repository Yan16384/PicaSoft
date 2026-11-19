"""Build one independent application; all outputs stay in the selected output folder."""
import argparse, base64, os, shutil, subprocess, sys
from pathlib import Path
from common import ROOT, project

def windows(key, output, install):
    cfg = project(key)
    if install:
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(ROOT / cfg["requirements"]), "pyinstaller==6.19.0", "Pillow>=10,<13"], check=True)
    entry = ROOT / cfg["directory"] / cfg["entry"]
    icon = entry.with_name("pica-icon.png")
    if not icon.is_file():
        raise FileNotFoundError(icon)
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed", "--noupx",
         "--name", cfg["artifact"], "--icon", str(icon), "--add-data", f"{icon}:.",
         "--distpath", str(output / "windows"), "--workpath", str(output / "work"),
         "--specpath", str(output / "spec"), str(entry)],
        cwd=entry.parent,
        check=True,
    )
    exe = output / "windows" / (cfg["artifact"] + ".exe")
    if not exe.is_file() or exe.stat().st_size < 1024: raise RuntimeError("Missing executable")
    with exe.open("rb") as f:
        if f.read(2) != b"MZ": raise RuntimeError("Invalid PE header")
    print(exe)

def android(key, output):
    cfg = project(key); folder = ROOT / cfg["android"]
    names = ["PICA_ANDROID_KEYSTORE_BASE64", "PICA_ANDROID_STORE_PASSWORD", "PICA_ANDROID_KEY_ALIAS", "PICA_ANDROID_KEY_PASSWORD"]
    present = [bool(os.getenv(n)) for n in names]
    if any(present) and not all(present): raise RuntimeError("All four Android signing secrets must be configured together")
    env = os.environ.copy()
    if all(present):
        signing = output / "private-signing"; signing.mkdir(parents=True, exist_ok=True)
        keystore = signing / "release.keystore"
        keystore.write_bytes(base64.b64decode(env["PICA_ANDROID_KEYSTORE_BASE64"], validate=True))
        env["PICA_ANDROID_KEYSTORE_FILE"] = str(keystore)
    mode = "release" if all(present) else "debug"
    gradle = shutil.which("gradle")
    if not gradle: raise RuntimeError("Gradle 8.2 is required (Actions installs it automatically)")
    subprocess.run([gradle, "-p", str(folder), "assemble" + mode.title(), "--no-daemon", "--console=plain"], env=env, check=True)
    source = folder / "app/build/outputs/apk" / mode / ("app-" + mode + ".apk")
    destination = output / "android"; destination.mkdir(parents=True, exist_ok=True)
    target = destination / (cfg["artifact"] + "-android-" + mode + ".apk")
    shutil.copy2(source, target)
    import zipfile
    with zipfile.ZipFile(target) as z:
        if "AndroidManifest.xml" not in z.namelist(): raise RuntimeError("Invalid APK")
    print(target)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("project"); parser.add_argument("--platform", choices=["windows", "android"], default="windows"); parser.add_argument("--output", type=Path); parser.add_argument("--install", action="store_true"); args = parser.parse_args()
    output = (args.output or ROOT / ".release-output" / args.project).resolve(); output.mkdir(parents=True, exist_ok=True)
    if args.platform == "windows": windows(args.project, output, args.install)
    else: android(args.project, output)
