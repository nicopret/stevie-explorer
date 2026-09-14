import asyncio
import logging
from datetime import UTC, datetime
from time import perf_counter

from dataclasses import replace

from stevie_explorer.capabilities.state import CommandResult
from stevie_explorer.devices.samsung import client as samsung_client
from stevie_explorer.devices import Device
from stevie_explorer.devices.samsung.client import get_api_v2, connect_remote_control, remote_control_connection
from stevie_explorer.devices.samsung.messages import has_application_list
from stevie_explorer.devices.samsung.keys import SAMSUNG_REMOTE_KEYS, remote_key_payload
from stevie_explorer.targets import Target

from stevie_explorer.capabilities.models import CapabilityCommand, CapabilityProbe, CapabilityResult, CapabilityStatus, ProbeMode, ProbeSafety, ProbeStrategy
from stevie_explorer.capabilities.registry import CapabilityCatalogue, ProbePack, ProbeRegistry
from stevie_explorer.capabilities.strategies import run_strategies
from stevie_explorer.identifiers import PayloadType, TransportType

SAMSUNG_INSTALLED_APPS = CapabilityProbe(
    probe_id = "samsung.apps.installed",
    name = "Samsung installed app discovery",
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
    tags = ("samsung", "tizen", "eden"),
    safety=ProbeSafety.READ_ONLY,
)

SAMSUNG_REMOTE_HOME = CapabilityProbe(
    probe_id="samsung.remote.home",
    name="Samsung remote home key",
    transport=TransportType.WEBSOCKET,
    payload_type=PayloadType.JSON,
    payload=remote_key_payload("KEY_HOME"),
    mode=ProbeMode.SEND_ONLY,
    description="Sends the Home remote key and changes the TV UI state",
    tags=("samsung", "tizen", "remote"),
    safety=ProbeSafety.STATE_CHANGE,
)

async def probe_api_v2(device: Device, target: Target, probe: CapabilityProbe, save_authentication) -> CapabilityResult:
    response = await get_api_v2(str(device.ip_address), timeout=probe.timeout)
    data = response.get("data")
    if not response.get("available"):
        status = CapabilityStatus.NO_RESPONSE
        error = response.get("error") or "Samsung API v2 did not respond"
    elif (
        isinstance(data, dict)
        and isinstance(data.get("device"), dict)
        and data["device"].get("type") == "Samsung SmartTV"
    ):
        status = CapabilityStatus.SUPPORTED
        error = None
    else:
        status = CapabilityStatus.NO_MATCH
        error = "Response did not contain valid Samsung API v2 device data"
    return CapabilityResult(
        target_id=target.target_id, probe_id=probe.probe_id, probe_name=probe.name,
        status=status, duration_ms=0, error=error,
    )


async def probe_remote_control(device: Device, target: Target, probe: CapabilityProbe, save_authentication) -> CapabilityResult:
    event = await connect_remote_control(device, target, probe.timeout, save_authentication)
    return CapabilityResult(
        target_id=target.target_id, probe_id=probe.probe_id, probe_name=probe.name,
        status=CapabilityStatus.SUPPORTED, duration_ms=0, matched_event=event,
    )


SAMSUNG_API_V2 = CapabilityProbe(
    probe_id="samsung.api.v2", name="Samsung API v2 availability",
    transport=TransportType.HTTP, payload_type=PayloadType.JSON, payload=None,
    runner=probe_api_v2,
)

SAMSUNG_REMOTE_CONTROL = CapabilityProbe(
    probe_id="samsung.remote.control", name="Samsung remote control availability",
    transport=TransportType.WEBSOCKET, payload_type=PayloadType.JSON, payload=None,
    expected_event="ms.channel.connect", runner=probe_remote_control,
)


SAMSUNG_PROBES = (
    SAMSUNG_API_V2,
    SAMSUNG_REMOTE_CONTROL,
    SAMSUNG_INSTALLED_APPS,
    SAMSUNG_REMOTE_HOME,
)

SAMSUNG_BASIC = ProbePack(
    pack_id="samsung.basic",
    name="Samsung safe basic discovery",
    probe_ids=(SAMSUNG_INSTALLED_APPS.probe_id,),
)

PROBE_REGISTRY = ProbeRegistry(SAMSUNG_PROBES, (SAMSUNG_BASIC,))


async def probe_installed_apps(device, target, probe, save_authentication):
    async with remote_control_connection(device, target, probe.timeout, save_authentication) as connection:
        attempts = await run_strategies(probe.strategies, connection.execute)
    successful = next((attempt for attempt in attempts if attempt.status == CapabilityStatus.SUPPORTED), None)
    failed = next((attempt for attempt in reversed(attempts) if attempt.status in (
        CapabilityStatus.ERROR, CapabilityStatus.REJECTED,
    )), None)
    return CapabilityResult(
        target_id=target.target_id, probe_id=probe.probe_id, probe_name=probe.name,
        status=CapabilityStatus.SUPPORTED if successful else failed.status if failed else CapabilityStatus.NO_RESPONSE,
        duration_ms=0,
        matched_event=successful.matched_event if successful else None,
        matched_strategy=successful.strategy_id if successful else None,
        strategy_attempts=attempts,
        error=None if successful else failed.error if failed else "No known application-list strategy produced a response",
    )


async def probe_remote_key(device, target, probe, save_authentication):
    if probe.command is None:
        raise ValueError("remote.key requires an explicit key command")
    # Validate before connecting, even when called directly outside the service.
    remote_key_payload(probe.command)
    async with remote_control_connection(device, target, probe.timeout, save_authentication) as connection:
        await connection.send_key(probe.command)
    # A successful send is protocol evidence, not independent physical confirmation.
    return CapabilityResult(
        target_id=target.target_id, probe_id=probe.probe_id, probe_name=probe.name,
        status=CapabilityStatus.SUPPORTED, duration_ms=0, matched_event=None,
    )


async def probe_remote_keys(device, target, probe, save_authentication, keys):
    """One connection, no replay; support denotes protocol success, not physical effect."""
    results = []
    log = logging.getLogger(__name__)
    try:
        async with remote_control_connection(device, target, probe.timeout, save_authentication) as connection:
            failed = False
            for index, key in enumerate(keys):
                if index and not failed:
                    await asyncio.sleep(samsung_client.REMOTE_KEY_DELAY)
                started = perf_counter()
                status, error = CapabilityStatus.SUPPORTED, None
                log.info("Testing Samsung remote key %s on target %s", key, target.target_id)
                try:
                    if failed:
                        raise ConnectionError("Connection unavailable")
                    async with asyncio.timeout(probe.timeout):
                        await connection.send_key(key)
                except samsung_client.SamsungCommandRejected:
                    status, error = CapabilityStatus.UNSUPPORTED, "Samsung rejected the command"
                except Exception:
                    status, error = CapabilityStatus.ERROR, "Samsung connection or key send failed"
                    failed = True
                results.append(CommandResult(
                    key=key, status=status, error=error,
                    last_checked=datetime.now(UTC), duration_ms=(perf_counter() - started) * 1000,
                ))
                log.info("Samsung remote key %s result=%s", key, status.value)
    except Exception:
        # Setup/authentication failure: no key sent. Preserve completed outcomes
        # if cleanup fails, and mark all unattempted commands as errors.
        for key in keys[len(results):]:
            results.append(CommandResult(
                key=key, status=CapabilityStatus.ERROR, last_checked=datetime.now(UTC),
                duration_ms=0, error="Samsung connection or authentication failed",
            ))
        log.warning("Samsung remote key batch connection failed on target %s", target.target_id)
    return tuple(results)


# Capability IDs are scoped to the configured target, distinct from target probes.

REMOTE_CONNECTION = replace(
    SAMSUNG_REMOTE_CONTROL, probe_id="remote.connection", name="Remote Control Connection",
)
INSTALLED_APPS = replace(
    SAMSUNG_INSTALLED_APPS, probe_id="apps.list", name="Installed Applications",
    runner=probe_installed_apps, payload=None, expected_event=None,
    timeout=SAMSUNG_REMOTE_CONTROL.timeout,
    strategies=tuple(
        ProbeStrategy(
            strategy_id=strategy_id,
            payload={
                "method": "ms.channel.emit",
                "params": {"event": event, "to": "host", "data": ""},
            },
            expected_event=event, response_matcher=has_application_list,
            timeout=SAMSUNG_REMOTE_CONTROL.timeout,
        )
        for strategy_id, event in (
            ("samsung.apps.installed_app", "ed.installedApp.get"),
            ("samsung.apps.eden_app", "ed.edenApp.get"),
        )
    ),
)
# ms.application.get queries a known app ID; it does not enumerate applications.
REMOTE_KEY = replace(
    SAMSUNG_REMOTE_HOME, probe_id="remote.key", name="Remote Key", payload=None,
    runner=probe_remote_key, batch_runner=probe_remote_keys,
    commands=tuple(CapabilityCommand(name, label) for name, label in SAMSUNG_REMOTE_KEYS.items()),
    description="Explicit key send; success confirms transport/protocol acceptance, not physical effect",
)
CAPABILITY_CATALOGUE = CapabilityCatalogue()
CAPABILITY_CATALOGUE.register("samsung.remote.control", (REMOTE_CONNECTION, INSTALLED_APPS, REMOTE_KEY))
