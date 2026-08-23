from __future__ import annotations

from app.db.models import DeviceFamily
from app.devices.base import ConnectionSpec, ZyxelDriver
from app.devices.transport import DriverError
from app.devices.zyxel_drivers import (
    ZyxelAPDriver,
    ZyxelFirewallDriver,
    ZyxelSwitchDriver,
)

_DRIVERS: dict[str, type[ZyxelDriver]] = {
    DeviceFamily.SWITCH.value: ZyxelSwitchDriver,
    DeviceFamily.FIREWALL.value: ZyxelFirewallDriver,
    DeviceFamily.AP.value: ZyxelAPDriver,
}


def get_driver_class(family: str) -> type[ZyxelDriver]:
    try:
        return _DRIVERS[family]
    except KeyError:
        raise DriverError(
            f"Unknown device family '{family}'. Supported: {', '.join(_DRIVERS)}"
        ) from None


def make_driver(spec: ConnectionSpec, family: str) -> ZyxelDriver:
    return get_driver_class(family)(spec)
