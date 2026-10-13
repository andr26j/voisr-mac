"""TCC permission probes and their System Settings deep links.

Every probe returns True (granted), False (denied), None (not asked yet), or
raises — callers get None for "unknown". Unknown must never be presented as granted: a wrongly
green chip is how someone spends twenty minutes debugging a dead hotkey.
"""

_PREFIX = "x-apple.systempreferences:com.apple.preference.security?"

PANES = {
    "microphone": ("Microphone", _PREFIX + "Privacy_Microphone"),
    "input_monitoring": ("Input Monitoring", _PREFIX + "Privacy_ListenEvent"),
    "accessibility": ("Accessibility", _PREFIX + "Privacy_Accessibility"),
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


def settings_url(key: str):
    pane = PANES.get(key)
    return pane[1] if pane else None


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
