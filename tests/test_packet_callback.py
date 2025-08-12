from datetime import datetime, timezone
import pytest
from unittest.mock import MagicMock
import zigpy.types as t
from .conftest import make_app, make_ieee
@pytest.fixture


async def app():
    """Create an async app fixture."""
    app = make_app({})
    await app._load_db()
    return app

def make_packet(src_address=None, **kwargs):

    
    """Create a test ZigbeePacket with sensible defaults."""
    defaults = {
        "timestamp": datetime.now(timezone.utc),
        "src": src_address or t.AddrModeAddress(addr_mode=t.AddrMode.NWK, address=0x1234),
        "src_ep": 1,
        "dst": t.AddrModeAddress(addr_mode=t.AddrMode.NWK, address=0x0000),
        "dst_ep": 1,
        "tsn": 123,
        "profile_id": 260,
        "cluster_id": 6,
        "data": t.SerializableBytes(b"test"),
        "lqi": 255,
        "rssi": -30,
    }
    defaults.update(kwargs)
    return t.ZigbeePacket(**defaults)
@pytest.mark.parametrize("filter_address", [
    None,  # Global callback
    t.AddrModeAddress(addr_mode=t.AddrMode.NWK, address=0x1234),
    t.AddrModeAddress(addr_mode=t.AddrMode.IEEE, address=make_ieee()),  # Use existing helper
])
async def test_register_packet_callback(app, filter_address):
    """Test registering and canceling packet callbacks with different filters."""
    callback = MagicMock()
    cancel = app.register_packet_callback(filter_address, callback)
    assert callable(cancel)
    assert callback in app._packet_callbacks[filter_address]
    cancel()
    assert callback not in app._packet_callbacks[filter_address]
    # Calling cancel again should not raise an error
    cancel()
@pytest.mark.parametrize("src_address,should_trigger", [
    (t.AddrModeAddress(addr_mode=t.AddrMode.NWK, address=0x1234), True),
    (t.AddrModeAddress(addr_mode=t.AddrMode.NWK, address=0x5678), False),
])
async def test_notify_packet_callbacks_address_filtering(app, src_address, should_trigger):
    """Test that address-specific callbacks filter correctly."""
    callback = MagicMock()
    filter_address = t.AddrModeAddress(addr_mode=t.AddrMode.NWK, address=0x1234)
    app.register_packet_callback(filter_address, callback)
    packet = make_packet(src_address=src_address)
    app.notify_packet_callbacks(packet)
    if should_trigger:
        callback.assert_called_once_with(packet)
    else:
        callback.assert_not_called()
async def test_notify_packet_callbacks_global(app):
    """Test that global callbacks are called for all packets."""
    callback = MagicMock()
    app.register_packet_callback(None, callback)
    packet = make_packet()
    app.notify_packet_callbacks(packet)
    callback.assert_called_once_with(packet)
async def test_notify_packet_callbacks_both_global_and_specific(app):
    """Test that both global and address-specific callbacks work together."""
    global_callback = MagicMock()
    specific_callback = MagicMock()
    src_address = t.AddrModeAddress(addr_mode=t.AddrMode.IEEE, address=make_ieee())
    app.register_packet_callback(None, global_callback)
    app.register_packet_callback(src_address, specific_callback)
    packet = make_packet(src_address=src_address)
    app.notify_packet_callbacks(packet)
    global_callback.assert_called_once_with(packet)
    specific_callback.assert_called_once_with(packet)
async def test_notify_packet_callbacks_exception_handling(app, caplog):
    """Test that exceptions in callbacks don't break other callbacks."""
    def failing_callback(packet):
        raise ValueError("Test exception")
    working_callback = MagicMock()
    app.register_packet_callback(None, failing_callback)
    app.register_packet_callback(None, working_callback)
    packet = make_packet()
    app.notify_packet_callbacks(packet)
    # Working callback should still be called despite the exception
    working_callback.assert_called_once_with(packet)
    # Check that error was logged (more flexible approach)
    assert any("packet callback" in record.message.lower() for record in caplog.records)


async def test_packet_received_triggers_callbacks(app):
    """Test that packet_received() actually calls notify_packet_callbacks()."""
    callback = MagicMock()
    app.register_packet_callback(None, callback)
    
    # Create a packet and call packet_received (like real Zigbee packets would)
    packet = make_packet()
    app.packet_received(packet)
    
    # Verify the callback was triggered through the real packet reception flow
    callback.assert_called_once_with(packet)