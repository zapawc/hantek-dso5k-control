"""Claim and release a single matching WinUSB interface on explicit request."""
from contextlib import contextmanager, ExitStack
from types import SimpleNamespace

from ._host import Blocked


@contextmanager
def open_link():
    import usb.core
    import usb.util
    from usb.backend import libusb1
    from libusb_package import get_library_path

    backend = libusb1.get_backend(find_library=lambda _: str(get_library_path()))
    if backend is None:
        raise Blocked("Could not load the libusb backend")
    found = list(usb.core.find(find_all=True, idVendor=0x049F, idProduct=0x505A, backend=backend))
    if len(found) != 1:
        raise Blocked("Expected one matching WinUSB device")
    device = found[0]
    with ExitStack() as cleanup:
        cleanup.callback(usb.util.dispose_resources, device)
        interface = device.get_active_configuration()[(0, 0)]
        endpoints = {ep.bEndpointAddress: (ep.bmAttributes & 3, ep.wMaxPacketSize) for ep in interface}
        if (interface.bInterfaceNumber, interface.bAlternateSetting) != (0, 0) or (
                any(endpoints.get(addr) != (2, 512) for addr in (0x81, 0x02))):
            raise Blocked("Unexpected USB interface or bulk endpoints")
        usb.util.claim_interface(device, 0)
        cleanup.callback(usb.util.release_interface, device, 0)
        yield SimpleNamespace(device=device, timeout_error=usb.core.USBTimeoutError)
