from collections.abc import Awaitable, Callable

from stevie_explorer.capabilities.models import CapabilityStatus, ProbeStrategy, StrategyAttempt


async def run_strategies(
    strategies: tuple[ProbeStrategy, ...],
    execute: Callable[[ProbeStrategy], Awaitable[StrategyAttempt]],
) -> tuple[StrategyAttempt, ...]:
    """Try ordered protocol alternatives using the caller's existing connection."""
    attempts = []
    for strategy in strategies:
        attempt = await execute(strategy)
        attempts.append(attempt)
        if attempt.status == CapabilityStatus.SUPPORTED or attempt.connection_failed:
            break
    return tuple(attempts)
