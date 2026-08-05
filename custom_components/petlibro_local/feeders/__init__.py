"""Per-model feeder profiles.

Petlibro's feeders do not share one command set. An auger feeder dispenses a
quantity of kibble; a wet feeder rotates a plate and holds a door open for a
duration, and does not implement the auger commands at all - the firmware drops
them silently, exactly as it drops a command that does not exist.

Rather than branch on model inside a shared class, each model contributes a
*profile*: the commands it handles, the capabilities it exposes, and how it
feeds. PetlibroDevice composes one and delegates to it.

Profiles satisfy FeederProfile structurally (typing.Protocol) - there is no base
class and nothing inherits. Shared behaviour lives in `common.py` as plain
functions that profiles call.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

_LOGGER = logging.getLogger(__name__)

# Handlers are called as handler(device, payload) - the same shape the device's
# own handler map uses, so the two merge without adaptation.
Handler = Callable[[Any, dict], Awaitable[None]]


@runtime_checkable
class FeederProfile(Protocol):
    """What a model must provide. Implementations do not inherit this."""

    #: Product ids this profile claims, e.g. {"PLAF203", "PLAF203S"}.
    PRODUCT_IDS: frozenset[str]

    #: Capability constants from const.py, used to decide which entities exist.
    CAPABILITIES: frozenset[str]

    #: Human-readable model name for the HA device registry.
    MODEL_NAME: str

    #: Model-specific wire-key -> state-key mappings. Take precedence over
    #: the shared maps, since the same key can mean different things per
    #: model (planId/execStep/finished differ on auger vs plate feeders).
    FIELDS: dict[str, str]

    # Capability-conditional members are deliberately NOT declared here. A
    # Protocol is structural and total: adding PLATE_COUNT would make every
    # non-plate profile fail `isinstance`, contradicting this module's promise
    # that profiles satisfy FeederProfile structurally. Those contracts live
    # with their capability constant in const.py instead - see
    # CAP_DISPENSE_PLATE.

    def build_plans(self, plans: list[dict]) -> str:
        """Serialise a plan list using this model's plan command."""
        ...

    def handlers(self) -> dict[str, Handler]:
        """Command handlers this model adds to the shared set."""
        ...

    # Feeding is deliberately NOT part of this protocol. An auger feeder
    # dispenses a quantity; a wet feeder runs a plate/door sequence with no
    # quantity at all, and the two need different state as well as different
    # arguments. Each profile exposes its own verb - DryFeeder.dispense(),
    # WetFeeder.serve_plate() - and entity platforms select by capability.


def _registry() -> list[FeederProfile]:
    # Imported lazily to keep module import order simple and avoid a cycle
    # back through device.py.
    from .dry import DryFeeder
    from .wet import WetFeeder

    return [DryFeeder(), WetFeeder()]


def get_profile(product_id: str | None) -> FeederProfile:
    """Return the profile for a product id.

    An unrecognised model gets `UnknownFeeder`: it parses and reports like an
    auger feeder but cannot dispense. Offering a Dispense button for hardware we
    have never seen risks a control that reports success and never feeds, since
    the firmware drops commands it does not implement without complaint.
    """
    pid = (product_id or "").upper()
    for profile in _registry():
        if pid in profile.PRODUCT_IDS:
            return profile

    from .unknown import UnknownFeeder

    if pid:
        _LOGGER.warning(
            "Unknown product id %s - falling back to a read-only profile. "
            "Sensors will work; feeding is disabled because a feeder that does "
            "not implement a command drops it silently, so a Dispense button "
            "would look like it worked. Adding a profile for this model is a "
            "small change - please open an issue with this product id.",
            pid,
        )
    return UnknownFeeder()
