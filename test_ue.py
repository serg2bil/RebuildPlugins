"""Self-check for the non-trivial bits: descriptor fixes and the retry ladder.

    python test_ue.py
"""
import json
import tempfile
from pathlib import Path

import ue


class FakeEngine(ue.Engine):
    def platforms(self):
        return {"Win64", "Linux", "Mac", "Android", "IOS"}


ENGINE = FakeEngine("5.8", Path("C:/nowhere"))


def _plugin(data, tmp):
    p = Path(tmp) / "Demo.uplugin"
    p.write_text(json.dumps(data), "utf-8")
    return ue.Plugin(p, json.loads(p.read_text("utf-8")))


def test_descriptor_fixes():
    with tempfile.TemporaryDirectory() as tmp:
        pl = _plugin({
            "EngineVersion": "4.27.0",
            "Installed": True,
            "Modules": [{"Name": "Demo",
                         "WhitelistPlatforms": ["Win32", "Win64", "HoloLens"],
                         "BlacklistTargets": ["Server"]}],
        }, tmp)
        fixes = ue.fix_descriptor(pl, ENGINE)
        on_disk = json.loads(pl.path.read_text("utf-8"))
        mod = on_disk["Modules"][0]

        assert "EngineVersion" not in on_disk, on_disk
        assert "Installed" not in on_disk, on_disk
        assert mod["PlatformAllowList"] == ["Win64"], mod
        assert "WhitelistPlatforms" not in mod, mod
        assert mod["TargetDenyList"] == ["Server"], mod
        assert len(fixes) == 5, fixes


def test_empty_allow_list_is_dropped():
    """All platforms dead -> removing the key beats leaving "build nowhere"."""
    with tempfile.TemporaryDirectory() as tmp:
        pl = _plugin({"Modules": [{"Name": "Demo", "WhitelistPlatforms": ["Win32"]}]}, tmp)
        ue.fix_descriptor(pl, ENGINE)
        mod = json.loads(pl.path.read_text("utf-8"))["Modules"][0]
        assert "PlatformAllowList" not in mod, mod


def test_no_change_no_rewrite():
    with tempfile.TemporaryDirectory() as tmp:
        pl = _plugin({"FriendlyName": "Demo", "Modules": [{"Name": "Demo"}]}, tmp)
        assert ue.fix_descriptor(pl, ENGINE) == []


def test_content_only():
    with tempfile.TemporaryDirectory() as tmp:
        assert _plugin({"FriendlyName": "Art"}, tmp).content_only
        code = _plugin({"Modules": [{"Name": "Demo"}]}, tmp)
        assert code.content_only          # no Source/ dir yet
        (Path(tmp) / "Source").mkdir()
        assert not ue.Plugin(code.path, code.data).content_only


def _args(**over):
    a = {"platforms": {"Win64", "Android"}, "deps": [], "extra": ["-nocompileuat"], "siblings": []}
    a.update(over)
    return a


def test_rule_drops_platform_with_missing_sdk():
    out = ["UnrealBuildTool: Android SDK not found. Please install it via the SDK manager."]
    name, m, mutate = ue.pick_fix(out, set())
    args = _args()
    assert mutate(args, m) is True
    assert args["platforms"] == {"Win64"}, args
    assert "SDK" in name


def test_rule_keeps_last_platform():
    """Never strip the only platform left - that would build nothing and 'succeed'."""
    out = ["ERROR: Win64 toolchain not found"]
    args = _args(platforms={"Win64"})
    hit = ue.pick_fix(out, set())
    if hit:                                # only if the rule matches Win64 at all
        assert hit[2](args, hit[1]) is False
    assert args["platforms"] == {"Win64"}


def test_rule_adds_dependency():
    sib = Path("C:/plugins/Foo/Foo.uplugin")
    out = ["ERROR: Unable to find plugin 'Foo' (referenced via Demo.uplugin)."]
    name, m, mutate = ue.pick_fix(out, set())
    args = _args(siblings=[sib])
    assert mutate(args, m) is True
    assert args["deps"] == [sib]
    assert mutate(args, m) is False        # idempotent


def test_rule_adds_strict_includes():
    out = ["Demo.cpp(12): error C2065: 'FFoo': undeclared identifier"]
    name, m, mutate = ue.pick_fix(out, set())
    args = _args()
    assert mutate(args, m) is True
    assert "-StrictIncludes" in args["extra"]


def test_tried_rules_are_not_repeated():
    out = ["Demo.cpp(12): error C2065: 'FFoo': undeclared identifier"]
    name, _, _ = ue.pick_fix(out, set())
    assert ue.pick_fix(out, {name}) is None


def test_extract_errors():
    lines = [
        "LogInit: building",
        "  ERROR: Missing -Plugin=... argument",
        "Demo.cpp(3): error C2065: 'X': undeclared identifier",
        "Demo.cpp(3): error C2065: 'X': undeclared identifier",   # deduped
        "LogInit: still fine",
        "Demo.cpp(9): warning C4100: unreferenced",               # warnings stay out
    ]
    errors = ue.extract_errors(lines)
    assert len(errors) == 2, errors
    assert "warning" not in " ".join(errors)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(vars().items()) if k.startswith("test_")]:
        fn()
        print("ok", fn.__name__)
    print("all good")
