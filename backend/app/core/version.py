from __future__ import annotations

APP_NAME = "Zynk"
__version__ = "0.5.0"
LICENSE = "MIT"
REPOSITORY = "https://github.com/Scout064/zynk"

SUPPORTED_FAMILIES = [
    {
        "family": "switch",
        "label": "Switches",
        "platform": "ZyNOS / FaOS",
        "verified_models": "XS1930-12HP, CX4800-56F",
        "config_pull": "show running-config",
        "revert_supported": True,
        "revert_note": "TFTP + copy tftp config + reload config (device reboots)",
        "eol": False,
    },
    {
        "family": "firewall",
        "label": "Firewalls",
        "platform": "uOS",
        "verified_models": "USG FLEX 700H",
        "config_pull": "show config running | no-pager",
        "revert_supported": False,
        "revert_note": "not in alpha (needs file staged on device)",
        "eol": False,
    },
    {
        "family": "zld_firewall",
        "label": "Firewalls (ZLD)",
        "platform": "ZLD",
        "verified_models": "ATP800, USG ZyWALL",
        "config_pull": "show running-config",
        "revert_supported": True,
        "revert_note": "FTP upload + apply /conf/<file> ignore-error rollback + write",
        "eol": True,
    },
    {
        "family": "ap",
        "label": "Access Points",
        "platform": "ZyNOS",
        "verified_models": "WBE660S",
        "config_pull": "show running-config",
        "revert_supported": True,
        "revert_note": "FTP upload + apply running-config + write",
        "eol": False,
    },
]
