"""Known remote-key commands. Inclusion does not prove support on a particular TV."""

SAMSUNG_REMOTE_KEYS = {
    "KEY_HOME": "Home",
    "KEY_RETURN": "Back",
    "KEY_ENTER": "Enter",
    "KEY_UP": "Up",
    "KEY_DOWN": "Down",
    "KEY_LEFT": "Left",
    "KEY_RIGHT": "Right",
    "KEY_VOLUP": "Volume Up",
    "KEY_VOLDOWN": "Volume Down",
    "KEY_MUTE": "Mute",
    "KEY_CHUP": "Channel Up",
    "KEY_CHDOWN": "Channel Down",
    "KEY_PLAY": "Play",
    "KEY_PAUSE": "Pause",
    "KEY_STOP": "Stop",
    "KEY_FF": "Fast Forward",
    "KEY_REWIND": "Rewind",
    "KEY_SOURCE": "Source",
    "KEY_GUIDE": "Guide",
    "KEY_INFO": "Info",
    **{f"KEY_{number}": str(number) for number in range(10)},
}


def remote_key_payload(key: str) -> dict:
    if key not in SAMSUNG_REMOTE_KEYS:
        raise ValueError(f"Unknown Samsung remote key '{key}'")
    return {
        "method": "ms.remote.control",
        "params": {
            "Cmd": "Click",
            "DataOfCmd": key,
            "Option": "false",
            "TypeOfRemote": "SendRemoteKey",
        },
    }
