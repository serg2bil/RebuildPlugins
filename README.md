# Rebuild Plugins

[![Download](https://img.shields.io/badge/Download-brightgreen)](https://github.com/serg2bil/RebuildPlugins/releases/download/Unreal/Rebuild.Plugins.exe)

![Logo](public/icon.png)

Batch-rebuilds Unreal Engine plugins for another engine version, and **repairs the
things that usually make those builds fail** instead of just reporting "error".

## Why

`RunUAT BuildPlugin` fails on a large share of marketplace plugins for reasons that
have nothing to do with the plugin's code: a pinned `EngineVersion`, UE4-era
`WhitelistPlatforms`, read-only files from the vault, stale `Binaries/`, or an
Android SDK you never installed. This tool fixes those first, and when a build
still fails it shows you the actual compiler error.

## What it fixes automatically

Before the build, on its own working copy (your originals are never touched):

| Problem | Fix |
|---|---|
| `"EngineVersion": "4.27.0"` pins the plugin to an old engine | field removed (UAT stamps the right one) |
| `"Installed": true` | removed |
| `WhitelistPlatforms` / `BlacklistTargets` / … (UE4 names) | renamed to `PlatformAllowList` / `TargetDenyList` / … |
| `Win32`, `HoloLens`, `Lumin` in the platform lists | dropped; an allow-list left empty is removed rather than meaning "build nowhere" |
| Read-only files (marketplace vault, ZIP extraction) | write bit cleared |
| Stale `Binaries/`, `Intermediate/` | deleted |
| Content-only plugin (no C++ modules) | copied straight to the output — `BuildPlugin` cannot build one |
| Long output paths hitting Windows `MAX_PATH` | built in a short temp dir, moved to the output on success |

After a failed build it reads the output and retries once with a matching fix:

| Symptom in the log | Retry |
|---|---|
| `<Platform> SDK not found` | drop that platform (never the last one) |
| `Unable to find plugin 'X'` | pass the sibling plugin via `-Dependencies=` |
| `error C2065 / C3861 / undeclared identifier` | rebuild with `-StrictIncludes` (no unity, no PCH) |
| AutomationTool failed to compile | rebuild without `-nocompileuat` |

Anything left is a genuine source incompatibility. The app then shows the exact
`file(line): error Cxxxx: message` — decoded from the MSVC codepage, so localised
errors are readable — and keeps the full log per plugin (double-click the row).

## The window

Two permanent zones, two contextual ones - no stack of equal-weight bands.

    chrome      the target (engine, platforms) sits with the action it configures
    run line    the chrome's bottom border, 1px idle, 3px of progress while building
    workspace   the plugin list, full width, full height - the product
    console     a drawer, opened per plugin, closed by default

One dark scale throughout: every surface is a step on it and every border is the same
line, so no part of the window belongs to a different theme.

**The list is the application.** Each row is two lines: the plugin's name, then its
version, the migration it is making (`4.27 → 5.8`) and the verdict. A status bar runs
down the left edge of every row, so state reads by position and colour at once. A row
that failed is physically taller and carries the compiler error in monospace, in place
- no click, no navigation, no other pane.

**The console is not furniture.** `Log ›` on a row opens the bottom drawer, titled with
that plugin's name, tailing that plugin's log file. Close it and the list is whole again.

**One dominant action.** `Rebuild 12` is the only saturated element in the window.

**The failure count is the filter.** Click `✗ 2 failed` to show only the failures.
Right-click a row to open its log or drop it from the list.

## Features

- Finds every engine on the machine: Epic Launcher manifest, registry, and a disk
  scan - including installs outside `Epic Games` and source builds.
- Sources: a folder of plugins, or ZIP archives. Your originals are never touched;
  everything happens on a copy under `Work/`.
- Only `Win64` is enabled by default, which is what makes most builds pass.
- A log file per plugin under `BuildLogs/`.
- English and Russian.

## Requirements

- Windows (Linux/macOS work for everything except the registry lookup)
- Unreal Engine with its prerequisites (Visual Studio with the C++ game dev workload)
- Python 3.9+ and `Pillow`, if running from source
- `PyInstaller` 5.13.2 for building the exe (other versions trip antivirus heuristics)

## Run

```bash
python main.py
```

```bash
python test_ue.py
```

```bash
pyinstaller BuildMain.spec
```

## Layout

| File | Role |
|---|---|
| `ue.py` | engine discovery, `.uplugin` repair, UAT build + retry ladder. No GUI imports. |
| `main.py` | Tk front end. Worker thread talks to it through one queue. |
| `test_ue.py` | self-check for the descriptor fixes and the retry rules |
