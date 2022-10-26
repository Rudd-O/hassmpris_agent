#!/usr/bin/python3

""" Example of announcing a service (in this case, a fake HTTP server) """

import asyncio
import socket
import os
import logging
import netifaces
import threading
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
                ipv6_addresses.append(addr["addr"].split("%")[0])
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
        % (os.getenv("USER"), socket.gethostname().split(".")[0]),
        server="hassmpris-%s.local." % guid,
        port=mpris_port,
        properties=desc,
        parsed_addresses=ipv4addr + ipv6addr,
    )
    return service


class Publisher(threading.Thread):
    def __init__(self, mpris_port: int, cakes_port: int) -> None:
        threading.Thread.__init__(self, name="mDNS", daemon=True)
        self.aiozc = AsyncZeroconf(ip_version=zeroconf.IPVersion.All)
        self.service = _service_record(mpris_port, cakes_port)

    def run(self) -> None:
        asyncio.run(self._publish())

    async def _publish(self) -> None:
        _LOGGER.debug("Publishing service record.")
        await self.aiozc.async_register_service(self.service)
        _LOGGER.debug("Published service record.")

    async def _unpublish(self) -> None:
        _LOGGER.debug("Unpublishing service record.")
        await self.aiozc.async_unregister_all_services()
        await self.aiozc.async_close()
        _LOGGER.debug("Unpublished service record.")

    def stop(self) -> None:
        asyncio.run(self._unpublish())
        self.join()


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    a = Publisher(40052, 40051)
    a.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    a.stop()
