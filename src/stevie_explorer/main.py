import asyncio

from stevie_explorer.api import ApiService
from stevie_explorer.capabilities import CapabilityProbeService
from stevie_explorer.capture import CaptureService
from stevie_explorer.config import Configuration
from stevie_explorer.eventbus import EventBus
from stevie_explorer.kernel import ExplorerKernel
from stevie_explorer.sessions import SessionManager
from stevie_explorer.targets import TargetRegistry
from stevie_explorer.telemetry import TelemetryService
from stevie_explorer.devices import DeviceRegistry

async def main() -> None:
    kernel = ExplorerKernel()
    kernel.install_signal_handlers()

    configuration = Configuration()
    eventbus = EventBus()
    telemetry = TelemetryService(eventbus)
    device_registry = DeviceRegistry(
        kernel,
        configuration.settings.device_registry_path,
        configuration.settings.device_registry_poll_interval,
    )
    
    api = ApiService(kernel)
    session_manager = SessionManager(kernel)
    target_registry = TargetRegistry(kernel)

    capture_service = CaptureService(session_manager = session_manager)
    capability_probe = CapabilityProbeService(kernel, state_path=configuration.settings.capabilities_file)

    kernel.register(configuration)
    kernel.register(eventbus)
    kernel.register(target_registry)
    kernel.register(telemetry)
    kernel.register(device_registry)
    kernel.register(session_manager)
    kernel.register(capture_service)
    kernel.register(capability_probe)
    kernel.register(api)

    await kernel.start()

    try:
        await kernel.wait_until_stoppped()
    finally:
        await kernel.stop()

if __name__ == "__main__":
    asyncio.run(main())
