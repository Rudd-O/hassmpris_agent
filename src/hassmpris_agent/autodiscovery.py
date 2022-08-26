#!/usr/bin/python3

""" Example of announcing a service (in this case, a fake HTTP server) """

import asyncio
import socket
import os
import logging
import netifaces
import uuid
import time
from typing import Any, Tuple


import zeroconf
from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf


_LOGGER = logging.getLogger(__name__)


def get_ip_addresses() -> Tuple[list[Any], list[Any]]:
    addresses = []
    ipv6_addresses = []
    for iface in netifaces.interfaces():
        addrs = netifaces.ifaddresses(iface)
        if netifaces.AF_INET in addrs:
            for addr in addrs[netifaces.AF_INET]:
                addresses.append(addr["addr"])
        if netifaces.AF_INET6 in addrs:
            for addr in addrs[netifaces.AF_INET6]:
                ipv6_addresses.append(addr["addr"])
    return (addresses, ipv6_addresses)


def get_machine_id() -> str:
    try:
        with open("/etc/machine-id", "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return socket.gethostname()


def get_user() -> str:
    return os.getenv("USER", "unknown")


def get_mpris_uuid() -> uuid.UUID:
    mid = get_machine_id()
    uid = get_user()
    return uuid.uuid3(uuid.NAMESPACE_DNS, "%s@%s" % (uid, mid))


def _service_record(mpris_port: int, cakes_port: int) -> AsyncServiceInfo:
    desc = {"cakes_port": cakes_port}
    guid = get_mpris_uuid()
    ipv4addr, ipv6addr = get_ip_addresses()
    service = AsyncServiceInfo(
        "_hassmpris._tcp.local.",
        "MPRIS on %s@%s._hassmpris._tcp.local."
        % (os.getenv("USER"), socket.gethostname()),
        server="hassmpris-%s.local." % guid,
        port=mpris_port,
        properties=desc,
        parsed_addresses=ipv4addr + ipv6addr,
    )
    return service


class Publisher:
    def __init__(self, mpris_port: int, cakes_port: int) -> None:
        self.aiozc = AsyncZeroconf(ip_version=zeroconf.IPVersion.All)
        self.services = [_service_record(mpris_port, cakes_port)]

    async def publish(self) -> None:
        _LOGGER.debug("Publishing service record.")
        svcs = self.services
        tasks = [self.aiozc.async_register_service(info) for info in svcs]
        background_tasks = await asyncio.gather(*tasks)
        await asyncio.gather(*background_tasks)
        _LOGGER.debug("Published service record.")

    async def unpublish(self) -> None:
        _LOGGER.debug("Unpublishing service record.")
        await self.aiozc.async_unregister_all_services()
        await self.aiozc.async_close()
        _LOGGER.debug("Unpublished service record.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    a = Publisher(40052, 40051)
    asyncio.run(a.publish())
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    asyncio.run(a.unpublish())
