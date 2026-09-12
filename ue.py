"""Unreal Engine discovery, .uplugin handling and UAT plugin builds.

No GUI dependencies on purpose: everything here is importable and testable.
"""
import json
import locale
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- engines

@dataclass(frozen=True)
class Engine:
    version: str
    root: Path

    @property
    def uat(self) -> Path:
        name = "RunUAT.bat" if os.name == "nt" else "RunUAT.sh"
        return self.root / "Engine" / "Build" / "BatchFiles" / name

    @property
    def major(self) -> int:
        return int(self.version.split(".")[0])

    def platforms(self) -> set:
        """Target platforms this engine knows about (used to strip dead ones)."""
        base = {"Win64", "Linux", "LinuxArm64", "Mac"}
        d = self.root / "Engine" / "Source" / "Programs" / "UnrealBuildTool" / "Platform"
        if d.is_dir():
            base |= {p.name for p in d.iterdir() if p.is_dir()}
        return base


def _version_of(root: Path) -> str:
    """Read Engine/Build/Build.version, falling back to the UE_x.y folder name."""
    try:
        v = json.loads((root / "Engine" / "Build" / "Build.version").read_text("utf-8-sig"))
        return f"{v['MajorVersion']}.{v['MinorVersion']}"
    except Exception:
        m = re.search(r"UE[_-]?(\d+\.\d+)", root.name)
        return m.group(1) if m else root.name


def find_engines() -> list:
    """All engines on this machine: Launcher manifest, registry, then a disk scan.

    The old scan-for-"Epic Games"-folders approach misses custom install paths
    (e.g. D:\\Unreal\\UE_5.8) and source builds entirely.
    """
    roots = []

    # 1. Epic Launcher manifest - authoritative for Launcher installs anywhere.
    dat = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
    dat = dat / "Epic" / "UnrealEngineLauncher" / "LauncherInstalled.dat"
    try:
        for item in json.loads(dat.read_text("utf-8-sig")).get("InstallationList", []):
            if item.get("AppName", "").startswith("UE_"):
                roots.append(Path(item["InstallLocation"]))
    except Exception:
        pass

    # 2. Registry: source/custom builds register themselves here.
    if os.name == "nt":
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Epic Games\Unreal Engine\Builds"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\EpicGames\Unreal Engine")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    n_keys, n_vals, _ = winreg.QueryInfoKey(k)
                    for i in range(n_vals):
                        roots.append(Path(winreg.EnumValue(k, i)[1]))
                    for i in range(n_keys):
                        sub = winreg.EnumKey(k, i)
                        with winreg.OpenKey(k, sub) as sk:
                            try:
                                roots.append(Path(winreg.QueryValueEx(sk, "InstalledDirectory")[0]))
                            except OSError:
                                pass
            except OSError:
                pass

    # 3. Last resort: look for UE_* folders in the usual places.
    for base in _scan_dirs():
        try:
            for child in base.iterdir():
                if child.name.startswith("UE_"):
                    roots.append(child)
                elif child.is_dir():
                    for sub in child.iterdir():          # .../Epic Games/UE_5.4
                        if sub.name.startswith("UE_"):
                            roots.append(sub)
        except OSError:
            continue

    engines, seen = [], set()
    for root in roots:
        try:
            root = root.resolve()
        except OSError:
            continue
        if root in seen:
            continue
        seen.add(root)
        eng = Engine(_version_of(root), root)
        if eng.uat.is_file():
            engines.append(eng)
    engines.sort(key=lambda e: [int(x) for x in re.findall(r"\d+", e.version)] or [0], reverse=True)
    return engines


def _scan_dirs():
    if os.name != "nt":
        return [Path.home(), Path("/Applications"), Path("/usr/local")]
    import string
    out = []
    for letter in string.ascii_uppercase:
        drive = Path(f"{letter}:\\")
        if drive.exists():
            out += [drive, drive / "Program Files", drive / "Epic Games", drive / "Unreal"]
    return out


UNKNOWN = "—"          # em dash: the descriptor did not say

# ---------------------------------------------------------------- plugins

@dataclass
class Plugin:
    path: Path                       # the .uplugin file
    data: dict = field(default_factory=dict)

    @property
    def dir(self) -> Path:
        return self.path.parent

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def friendly(self) -> str:
        return self.data.get("FriendlyName") or self.name

    @property
    def version(self) -> str:
        return self.data.get("VersionName", UNKNOWN)

    @property
    def engine_version(self) -> str:
        return self.data.get("EngineVersion", UNKNOWN)

    @property
    def icon(self):
        p = self.dir / "Resources" / "Icon128.png"
        return p if p.is_file() else None

    @property
    def content_only(self) -> bool:
        """No C++ modules -> UAT BuildPlugin cannot and need not build it."""
        return not self.data.get("Modules") or not (self.dir / "Source").is_dir()


def find_plugins(root) -> list:
    """Find .uplugin files under root, without descending into found plugins."""
    root = Path(root)
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        upl = [f for f in filenames if f.endswith(".uplugin")]
        if upl:
            dirnames[:] = []                       # a plugin never nests another
            p = Path(dirpath) / upl[0]
            try:
                found.append(Plugin(p, json.loads(p.read_text("utf-8-sig"))))
            except (OSError, json.JSONDecodeError):
                found.append(Plugin(p, {}))
        else:
            dirnames[:] = [d for d in dirnames if d not in ("Intermediate", "Binaries", "Saved")]
    return found


# ------------------------------------------------------------ pre-build fixes

# UE5 renamed every whitelist/blacklist field. The engine still reads the old
# names through a deprecated fallback, but they are on the way out and some
# tooling already chokes on them - rename while we are here.
RENAMED_FIELDS = {
    "WhitelistPlatforms": "PlatformAllowList",
    "BlacklistPlatforms": "PlatformDenyList",
    "WhitelistTargets": "TargetAllowList",
    "BlacklistTargets": "TargetDenyList",
    "WhitelistTargetConfigurations": "TargetConfigurationAllowList",
    "BlacklistTargetConfigurations": "TargetConfigurationDenyList",
    "WhitelistPrograms": "ProgramAllowList",
    "BlacklistPrograms": "ProgramDenyList",
}


def unlock_tree(path: Path):
    """Clear read-only bits. Marketplace vault / ZIP copies are often read-only,
    which makes both our cleanup and UAT's own copy step fail with Access denied."""
    for p in Path(path).rglob("*"):
        try:
            if not os.access(p, os.W_OK):
                p.chmod(p.stat().st_mode | stat.S_IWRITE)
        except OSError:
            pass


def fix_descriptor(plugin: Plugin, engine: Engine) -> list:
    """Rewrite the .uplugin in place so it is acceptable to `engine`.

    Returns the list of applied fixes (empty if nothing needed changing).
    """
    data, fixes = plugin.data, []

    # An EngineVersion pinned to an older engine makes the plugin load as
    # "incompatible"; BuildPlugin stamps the right one on the output anyway.
    if data.pop("EngineVersion", None):
        fixes.append("dropped EngineVersion")
    # "Installed": true marks the plugin as shipped-with-the-engine.
    if data.pop("Installed", None):
        fixes.append("dropped Installed flag")

    valid = engine.platforms()
    for module in data.get("Modules") or []:
        for old, new in RENAMED_FIELDS.items():
            if old in module:
                module.setdefault(new, module.pop(old))
                module.pop(old, None)
                fixes.append(f"{module.get('Name', '?')}: {old} -> {new}")
        for key in ("PlatformAllowList", "PlatformDenyList"):
            listed = module.get(key)
            if not isinstance(listed, list):
                continue
            kept = [p for p in listed if p in valid]
            if kept != listed:
                dropped = ", ".join(p for p in listed if p not in valid)
                if kept or key == "PlatformDenyList":
                    module[key] = kept
                else:
                    # An empty allow list means "build nowhere" - drop the key.
                    module.pop(key)
                fixes.append(f"{module.get('Name', '?')}: removed dead platform {dropped}")

    if fixes:
        plugin.path.write_text(json.dumps(data, indent=4, ensure_ascii=False), "utf-8")
    return fixes


def clean_tree(plugin_dir: Path) -> list:
    """Remove stale build artifacts that make UAT reuse the wrong binaries."""
    fixes = []
    for name in ("Binaries", "Intermediate"):
        target = Path(plugin_dir) / name
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            fixes.append(f"removed stale {name}/")
    return fixes


def prepare(plugin: Plugin, engine: Engine) -> list:
    """All the pre-flight repairs, applied to our working copy of the plugin."""
    unlock_tree(plugin.dir)
    return clean_tree(plugin.dir) + fix_descriptor(plugin, engine)


# ------------------------------------------------------------ error analysis

ERROR_LINE = re.compile(
    r"(?:^\s*ERROR:|error [A-Z]{1,3}\d+\s*:|: error\s*:|fatal error|Exception in |AutomationException)",
    re.IGNORECASE)

EXIT_CODE = re.compile(r"AutomationTool exiting with ExitCode=(\d+)(?: \((\w+)\))?")


def extract_errors(lines, limit=25) -> list:
    """Pull the lines a human actually needs out of a few thousand lines of UAT noise."""
    out = []
    for line in lines:
        line = line.rstrip()
        if ERROR_LINE.search(line) and line not in out:
            out.append(line)
    return out[:limit]


# Retry ladder. Each rule: (name, symptom regex, mutation of the UAT args).
# The mutation returns True when it actually changed something.

def _drop_platform(args: dict, match) -> bool:
    plat = match.group("plat")
    if plat in args["platforms"] and len(args["platforms"]) > 1:
        args["platforms"].remove(plat)
        return True
    return False


def _add(flag):
    def mutate(args, match):
        if flag in args["extra"]:
            return False
        args["extra"].append(flag)
        return True
    return mutate


def _remove(flag):
    def mutate(args, match):
        if flag not in args["extra"]:
            return False
        args["extra"].remove(flag)
        return True
    return mutate


def _add_dependency(args, match) -> bool:
    """`Unable to find plugin 'Foo'` -> hand UAT the sibling plugin as -Dependencies."""
    wanted = match.group("dep")
    for candidate in args["siblings"]:
        if candidate.stem.lower() == wanted.lower():
            if candidate in args["deps"]:
                return False
            args["deps"].append(candidate)
            return True
    return False


RULES = [
    ("install the missing SDK or skip the platform",
     re.compile(r"(?P<plat>Android|IOS|TVOS|Linux|LinuxArm64|Mac|VisionOS)[^\n]{0,80}"
                r"(SDK|toolchain)[^\n]{0,40}(not |isn't |could not be |un)(found|installed|located|available)",
                re.IGNORECASE),
     _drop_platform),

    ("missing plugin dependency",
     re.compile(r"Unable to find plugin '(?P<dep>[\w.\-]+)'", re.IGNORECASE),
     _add_dependency),

    ("unity/PCH build hides missing includes",
     re.compile(r"error (C2065|C2039|C3861|C2061)|was not declared in this scope|"
                r"undeclared identifier", re.IGNORECASE),
     _add("-StrictIncludes")),

    ("AutomationTool itself needs rebuilding",
     re.compile(r"(AutomationTool|UnrealBuildTool)[^\n]{0,60}(failed to compile|could not be (found|compiled))",
                re.IGNORECASE),
     _remove("-nocompileuat")),
]


def pick_fix(lines, tried):
    """First rule whose symptom appears in the output and hasn't been tried yet."""
    text = "\n".join(lines)
    for name, pattern, mutate in RULES:
        if name in tried:
            continue
        m = pattern.search(text)
        if m:
            return name, m, mutate
    return None


# ---------------------------------------------------------------- building

@dataclass
class BuildResult:
    plugin: str
    ok: bool
    fixes: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    log: Path = None
    attempts: int = 0


def _oem_encoding() -> str:
    """The codepage the MSVC toolchain uses for its messages."""
    if os.name == "nt":
        try:
            import ctypes
            return "cp" + str(ctypes.windll.kernel32.GetOEMCP())
        except Exception:
            pass
    return locale.getpreferredencoding(False)


OEM = _oem_encoding()


def decode_line(raw: bytes) -> str:
    """UBT speaks UTF-8, cl.exe speaks the OEM codepage - handle both, otherwise
    every localised compiler error reaches the user as mojibake."""
    for encoding in ("utf-8", OEM):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def run_uat(engine: Engine, plugin: Plugin, package: Path, args: dict, on_line) -> tuple:
    """Run BuildPlugin, streaming output. Returns (exit_code, lines)."""
    cmd = [str(engine.uat), "BuildPlugin",
           f"-Plugin={plugin.path}",
           f"-Package={package}",
           "-unversioned"]
    if args["platforms"]:
        cmd.append("-TargetPlatforms=" + "+".join(sorted(args["platforms"])))
    cmd += [f"-Dependencies={d}" for d in args["deps"]]
    cmd += args["extra"]

    on_line("> " + subprocess.list2cmdline(cmd))
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    lines = []
    for raw in proc.stdout:
        line = decode_line(raw).rstrip("\r\n")
        lines.append(line)
        on_line(line)
    return proc.wait(), lines


def build_plugin(plugin: Plugin, engine: Engine, out_dir: Path, on_line,
                 platforms=("Win64",), siblings=(), max_attempts=3) -> BuildResult:
    """Build one plugin, retrying with an auto-fix when the failure is a known one."""
    out_dir = Path(out_dir)
    result = BuildResult(plugin.friendly, ok=False)
    result.fixes += prepare(plugin, engine)

    if plugin.content_only:
        dest = out_dir / plugin.name
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(plugin.dir, dest)
        result.fixes.append("content-only plugin: copied, nothing to compile")
        result.ok = True
        on_line(f"{plugin.friendly}: content-only, copied to {dest}")
        return result

    args = {"platforms": set(platforms), "deps": [], "extra": ["-nocompileuat"],
            "siblings": list(siblings)}
    tried = set()

    # ponytail: package into a short temp path to stay clear of Windows MAX_PATH
    # (HostProject/Plugins/<name>/Source/... on top of a Desktop path overflows),
    # then move. Drop this if UE ever handles long paths everywhere.
    work_root = Path(tempfile.gettempdir()) / "upb"

    for attempt in range(1, max_attempts + 1):
        result.attempts = attempt
        work = work_root / plugin.name
        shutil.rmtree(work, ignore_errors=True)
        work.parent.mkdir(parents=True, exist_ok=True)

        code, lines = run_uat(engine, plugin, work, args, on_line)
        if code == 0:
            dest = out_dir / plugin.name
            out_dir.mkdir(parents=True, exist_ok=True)
            shutil.rmtree(dest, ignore_errors=True)
            shutil.move(str(work), str(dest))
            result.ok = True
            return result

        # UAT compiles a throwaway copy, so its paths mean nothing to the user.
        host_copy = str(work / "HostProject" / "Plugins" / plugin.name)
        result.errors = [e.replace(host_copy, str(plugin.dir)) for e in extract_errors(lines)]
        shutil.rmtree(work, ignore_errors=True)

        chosen = pick_fix(lines, tried)
        if not chosen or attempt == max_attempts:
            break
        name, match, mutate = chosen
        tried.add(name)
        if not mutate(args, match):
            tried.add(name)
            continue
        result.fixes.append(f"retry: {name}")
        on_line(f"-- auto-fix: {name}, retrying ({attempt + 1}/{max_attempts})")

    return result


def app_dir() -> Path:
    """Folder next to the executable (or the script), never PyInstaller's temp dir."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


if __name__ == "__main__":
    for e in find_engines():
        print(f"{e.version:8} {e.root}")
