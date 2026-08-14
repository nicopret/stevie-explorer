import asyncio

from stevie_explorer.api import ApiService
from stevie_explorer.capabilities import CapabilityProbeService
from stevie_explorer.config import Configuration
from stevie_explorer.eventbus import EventBus
from stevie_explorer.kernel import ExplorerKernel
from stevie_explorer.sessions import SessionManager
from stevie_explorer.targets import TargetRegistry
from stevie_explorer.telemetry import TelemetryService

async def main() -> None:
    kernel = ExplorerKernel()
    kernel.install_signal_handlers()

    configuration = Configuration()
    eventbus = EventBus()
    telemetry = TelemetryService(eventbus)
    
    api = ApiService(kernel)
    capability_probe = CapabilityProbeService(kernel)
    session_manager = SessionManager(kernel)
    target_registry = TargetRegistry(kernel)

    kernel.register(api)
    kernel.register(capability_probe)
    kernel.register(configuration)
    kernel.register(eventbus)
    kernel.register(target_registry)
    kernel.register(telemetry)
    kernel.register(session_manager)

    await kernel.start()

    try:
        await kernel.wait_until_stoppped()
    finally:
        await kernel.stop()

if __name__ == "__main__":
    asyncio.run(main())
