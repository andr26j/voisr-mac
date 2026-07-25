"""TCC permission probes and their System Settings deep links.

Every probe returns True (granted), False (denied), or raises — callers get
None for "unknown". Unknown must never be presented as granted: a wrongly
green chip is how someone spends twenty minutes debugging a dead hotkey.
"""

_PREFIX = "x-apple.systempreferences:com.apple.preference.security?"

PANES = {
    "microphone": ("Microphone", _PREFIX + "Privacy_Microphone"),
    "input_monitoring": ("Input Monitoring", _PREFIX + "Privacy_ListenEvent"),
    "accessibility": ("Accessibility", _PREFIX + "Privacy_Accessibility"),
}

ORDER = ["microphone", "input_monitoring", "accessibility"]


def microphone_granted() -> bool:
    """AVAuthorizationStatusAuthorized is 3."""
    import AVFoundation
    status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
        AVFoundation.AVMediaTypeAudio)
    return int(status) == 3


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


def states(probes=None) -> list[dict]:
    probes = probes if probes is not None else DEFAULT_PROBES
    out = []
    for key in ORDER:
        label, url = PANES[key]
        probe = probes.get(key)
        try:
            granted = bool(probe()) if probe is not None else None
        except Exception:
            granted = None  # unknown, never granted
        out.append({"key": key, "label": label,
                    "granted": granted, "settings_url": url})
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
            import AppKit
            AppKit.NSWorkspace.sharedWorkspace().openURL_(
                AppKit.NSURL.URLWithString_(u))
    try:
        opener(url)
    except Exception:
        return False
    return True
