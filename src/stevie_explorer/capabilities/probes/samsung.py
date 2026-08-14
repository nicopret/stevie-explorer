from stevie_explorer.capabilities.models import CapabilityProbe, ProbeMode
from stevie_explorer.identifiers import PayloadType, TransportType

SAMSUNG_INSTALLED_APPS = CapabilityProbe(
    probe_id = "samsung.apps.installed",
    name = "Samsung installed app discover",
    transport = TransportType.WEBSOCKET,
    payload_type=PayloadType.JSON,
    payload={
        "method": "ms.channel.emit",
        "params": {
            "event": "ed.installedApp.get",
            "to": "host"
        }
    },
    mode = ProbeMode.EXPECT_MATCH,
    expected_event ="ed.installedApp.get",
    timeout = 10.0,
    description = "Tests whether the TV exposes installed apps over the Samsung EDEN protocol",
    tags = ("samsung", "tizen", "eden", "read_only")
)

SAMSUNG_PROBES = (
    SAMSUNG_INSTALLED_APPS
)
