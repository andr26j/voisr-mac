"""TCC permission probes and their System Settings deep links.

Every probe returns True (granted), False (denied), None (not asked yet), or
raises — callers get None for "unknown". Unknown must never be presented as granted: a wrongly
green chip is how someone spends twenty minutes debugging a dead hotkey.
"""

_LEGACY = "x-apple.systempreferences:com.apple.preference.security?"
_MODERN = "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?"
_ANCHORS = {"microphone": "Privacy_Microphone",
            "input_monitoring": "Privacy_ListenEvent",
            "accessibility": "Privacy_Accessibility"}


def _macos_major() -> int:
    import platform
    try:
        return int(platform.mac_ver()[0].split(".")[0])
    except (ValueError, IndexError):
        return 0


def settings_url(key: str, macos_major=None):
    """Deep link to a key's Privacy & Security pane. macOS 26 moved the pane
    to the Settings extension id; older releases keep the preference pane."""
    anchor = _ANCHORS.get(key)
    if anchor is None:
        return None
    major = _macos_major() if macos_major is None else macos_major
    return (_MODERN if major >= 26 else _LEGACY) + anchor


PANES = {
    "microphone": ("Microphone", settings_url("microphone")),
    "input_monitoring": ("Input Monitoring", settings_url("input_monitoring")),
    "accessibility": ("Accessibility", settings_url("accessibility")),
}

ORDER = ["microphone", "input_monitoring", "accessibility"]


def hotkey_key(mode: str) -> str:
    """The pane whose grant the key tap needs in this mode (see keytap)."""
    return "accessibility" if mode == "active" else "input_monitoring"


def required_keys(mode: str) -> list[str]:
    """Panes Voisr needs in this mode: an active tap has no use for Input
    Monitoring, so it isn't shown (or asked for) at all."""
    if mode == "active":
        return [k for k in ORDER if k != "input_monitoring"]
    return list(ORDER)


def microphone_state(status: int):
    """AVAuthorizationStatus -> granted. 0 is "not determined": macOS has not
    asked yet and will on the first recording, so it is unknown, not denied.
    1 (restricted) and 2 (denied) are real refusals; 3 is authorized."""
    status = int(status)
    if status == 0:
        return None
    return status == 3


def microphone_granted():
    import AVFoundation
    return microphone_state(
        AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
            AVFoundation.AVMediaTypeAudio))


def input_monitoring_granted() -> bool:
    import Quartz
    return bool(Quartz.CGPreflightListenEventAccess())


def accessibility_granted() -> bool:
    import Quartz
    return bool(Quartz.CGPreflightPostEventAccess())


DEFAULT_PROBES = {
    "microphone": microphone_granted,
    "input_monitoring": input_monitoring_granted,
    "accessibility": accessibility_granted,
}


def states(probes=None, mode: str = "listen") -> list[dict]:
    probes = probes if probes is not None else DEFAULT_PROBES
    hotkey = hotkey_key(mode)
    out = []
    for key in required_keys(mode):
        label, url = PANES[key]
        probe = probes.get(key)
        try:
            granted = probe() if probe is not None else None
            granted = None if granted is None else bool(granted)
        except Exception:
            granted = None  # unknown, never granted
        out.append({"key": key, "label": label,
                    "granted": granted, "settings_url": url,
                    "hotkey": key == hotkey, "effective": True})
    return out


# tccutil service names for each pane key.
TCC_SERVICES = {"microphone": "Microphone", "input_monitoring": "ListenEvent",
                "accessibility": "Accessibility"}

# CFBundleIdentifier written by scripts/install_app.sh. Resetting anything
# else (python, Terminal) would wipe grants that belong to other apps.
OWN_BUNDLE_ID = "com.local.voisr"


def own_bundle_id():
    try:
        import Foundation
        return Foundation.NSBundle.mainBundle().bundleIdentifier()
    except Exception:
        return None


def _run_tccutil(argv) -> int:
    import subprocess
    return subprocess.run(argv, capture_output=True, timeout=10).returncode


def reset_grant(key: str, bundle_id=None, runner=None) -> dict:
    """Clear Voisr's own TCC entry for one pane so the user can grant it
    afresh -- the fix for a switch that shows ON while macOS refuses."""
    service = TCC_SERVICES.get(key)
    if service is None:
        return {"ok": False, "error": "unknown permission"}
    if bundle_id is None:
        bundle_id = own_bundle_id()
    if bundle_id != OWN_BUNDLE_ID:
        return {"ok": False,
                "error": "reset only works in the installed Voisr app"}
    runner = runner or _run_tccutil
    try:
        code = runner(["/usr/bin/tccutil", "reset", service, bundle_id])
    except Exception as error:
        return {"ok": False, "error": str(error)[:200]}
    if code != 0:
        return {"ok": False, "error": f"tccutil exited with {code}"}
    return {"ok": True}


def request_grant(key: str) -> None:
    """Ask macOS for one pane's grant again. After `tccutil reset` Voisr's
    row is gone from that list; asking re-registers it (switched off) so
    the pane the user is sent to has a Voisr switch to turn on. May show
    the system prompt. Never raises."""
    if key == "input_monitoring":
        try:
            import Quartz
            Quartz.CGRequestListenEventAccess()
        except Exception:
            pass
    elif key == "accessibility":
        try:
            import Quartz
            Quartz.CGRequestPostEventAccess()
        except Exception:
            pass
        try:
            from ApplicationServices import (AXIsProcessTrustedWithOptions,
                                             kAXTrustedCheckOptionPrompt)
            AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
        except Exception:
            pass
    elif key == "microphone":
        try:
            import AVFoundation
            AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                AVFoundation.AVMediaTypeAudio, lambda granted: None)
        except Exception:
            pass


def open_pane(key: str, opener=None) -> bool:
    """Open a pane by key. The key is validated against PANES so caller input
    can never reach NSWorkspace verbatim."""
    url = settings_url(key)
    if url is None:
        return False
    if opener is None:
        def opener(u):
            # open_pane is a bridge call reached from a pywebview worker
            # thread, so NSWorkspace.openURL_ must not run inline here --
            # dispatch it to the main thread like every other AppKit entry
            # point on this branch (ui.py's apply_app_appearance,
            # _run_save_panel, native_setup).
            from PyObjCTools import AppHelper
            import AppKit

            def go():
                try:
                    AppKit.NSWorkspace.sharedWorkspace().openURL_(
                        AppKit.NSURL.URLWithString_(u))
                except Exception:
                    pass

            AppHelper.callAfter(go)
    try:
        opener(url)
    except Exception:
        return False
    return True
