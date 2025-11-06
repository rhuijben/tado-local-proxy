"""Minimal AsyncZeroconf-only mDNS registration helper.

This module implements a single, predictable path: AsyncZeroconf. It logs
parameters, success and failures so runtime issues are easy to see.
"""
from typing import Dict, Optional
import asyncio
import logging

logger = logging.getLogger(__name__)

# module-level registration handle: ('async', async_zc, info)
_reg = None
_preferred_method = None


def _props_to_txt(props: Dict[str, str]):
    return {k: (v.encode('utf-8') if isinstance(v, str) else v) for k, v in props.items()}


def _pack_ipv4(addr: str):
    import socket as _socket
    try:
        return _socket.inet_pton(_socket.AF_INET, addr)
    except Exception:
        return None


def _get_primary_ipv4():
    """Return a best-effort primary IPv4 address for this host as a string, or None.

    We use a UDP socket connect trick which doesn't send packets but reveals the
    system-chosen outbound IP for the route. This works on most networks and
    avoids relying on hostname lookups which can be unreliable in containers.
    """
    import socket as _socket
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        # Destination doesn't need to be reachable; no traffic is sent for connect
        s.connect(("8.8.8.8", 80))
        addr = s.getsockname()[0]
        s.close()
        return addr
    except Exception:
        try:
            # Fallback: hostname lookup
            return _socket.gethostbyname(_socket.gethostname())
        except Exception:
            return None



async def register_service_async(name: str = 'tado-local', port: int = 4407, props: Optional[Dict[str, str]] = None, service_type: Optional[str] = None, advertise_addr: Optional[str] = None):
    """Register a service using AsyncZeroconf only.

    Returns (ok: bool, method: Optional[str], message: Optional[str]).
    """
    global _reg
    props = props or {}
    service_type = service_type or '_tado-local._tcp.local.'

    logger.debug("register_service_async called: name=%s port=%s service_type=%s props=%s", name, port, service_type, props)

    try:
        from zeroconf.asyncio import AsyncZeroconf
        from zeroconf import ServiceInfo
    except Exception as e:
        logger.exception("Async zeroconf not available")
        return False, None, f"zeroconf (async) not available: {e}"

    desc = _props_to_txt(props)

    # Determine address bytes to advertise. If the caller provided an
    # explicit advertise_addr use that; otherwise pick a sensible local IPv4.
    addresses = None
    try:
        addr_to_use = advertise_addr or _get_primary_ipv4()
        if addr_to_use:
            packed = _pack_ipv4(addr_to_use)
            if packed:
                addresses = [packed]
    except Exception:
        addresses = None

    info = ServiceInfo(
        service_type,
        f"{name}.{service_type}",
        addresses=addresses,
        port=port,
        properties=desc,
    )

    try:
        logger.debug("Attempting AsyncZeroconf registration for %s", name)
        async_zc = AsyncZeroconf()
        # Allow the zeroconf implementation to change the instance name if there
        # is a local name conflict. This prevents NonUniqueNameException and
        # results in the service being registered with a disambiguated name
        # (e.g. "tado-local (2)").
        await async_zc.async_register_service(info, allow_name_change=True)
        _reg = ('async', async_zc, info)
        # Log the actual name used in case zeroconf had to adjust it due to a
        # name conflict.
        try:
            actual_name = getattr(info, 'name', None) or getattr(info, 'server', None)
        except Exception:
            actual_name = None
        # Decode properties for readable logging
        try:
            decoded_props = {k: (v.decode('utf-8') if isinstance(v, (bytes, bytearray)) else v) for k, v in desc.items()}
        except Exception:
            decoded_props = props
        addr_str = None
        try:
            if addresses and len(addresses) > 0:
                import socket as _socket
                addr_str = _socket.inet_ntop(_socket.AF_INET, addresses[0])
        except Exception:
            addr_str = None

        # Also log the server target that will appear in the SRV record so
        # remote resolvers can debug why SRV/TXT/A lookups might fail.
        srv_target = getattr(info, 'server', None)
        logger.info(
            "AsyncZeroconf registered service %s (published as: %s) on port %s (addresses=%s srv=%s props=%s)",
            name, actual_name or name, port, addr_str, srv_target, decoded_props,
        )
        return True, 'zeroconf_async', None
    except Exception as e:
        logger.exception("AsyncZeroconf registration failed for %s", name)
        return False, None, str(e)


async def unregister_service_async():
    """Async unregister for the current registration (best-effort)."""
    global _reg
    if not _reg:
        return
    kind = _reg[0]
    try:
        if kind == 'async':
            async_zc, info = _reg[1], _reg[2]
            try:
                await async_zc.async_unregister_service(info)
            except Exception:
                pass
            try:
                await async_zc.async_close()
            except Exception:
                try:
                    import asyncio as _asyncio
                    await _asyncio.to_thread(getattr(async_zc, 'close', lambda: None))
                except Exception:
                    pass
    finally:
        _reg = None
