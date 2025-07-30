"""TouchLink commissioning and factory reset API."""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any, AsyncGenerator

import zigpy.types as t
from zigpy.zcl import foundation
from zigpy.zcl.clusters.lightlink import LightLink
from zigpy.zcl.foundation import ZCLHeader
import secrets
from zigpy.zcl.clusters.lightlink import ZigbeeInformation, ScanRequestInformation

LOGGER = logging.getLogger(__name__)

# TouchLink constants
TOUCHLINK_PROFILE_ID = 0xC25D # deep wiki said this 0xC05E
TOUCHLINK_SCAN_TIMEOUT = 5.0
TOUCHLINK_FACTORY_RESET_TIMEOUT = 3.0


class TouchLinkDevice:
    """Represents a discovered TouchLink device."""

    def __init__(self, response_data):
        self.transaction_id = response_data.inter_pan_transaction_id
        self.rssi_correction = response_data.rssi_correction
        self.zigbee_info = response_data.zigbee_info
        self.touchlink_info = response_data.touchlink_info
        self.key_bitmask = response_data.key_bitmask
        self.response_id = response_data.response_id
        self.epid = response_data.epid
        self.nwk_update_id = response_data.nwk_update_id
        self.logical_channel = response_data.logical_channel
        self.pan_id = response_data.pan_id
        self.nwk_addr = response_data.nwk_addr
        self.num_sub_devices = response_data.num_sub_devices
        self.total_group_ids = response_data.total_group_ids

    @property
    def is_factory_new(self) -> bool:  
        """Check if device is factory new."""
        return bool(self.touchlink_info.factory_new)

    def __repr__(self) -> str:
        return (
            f"TouchLinkDevice(epid={self.epid!r}, "
            f"pan_id=0x{self.pan_id:04x}, "
            f"nwk_addr=0x{self.nwk_addr:04x}, "
            f"factory_new={self.is_factory_new})"
        )

class TouchLinkManager:
    """Manages TouchLink operations."""

    def __init__(self, app):
        self._app = app

    async def scan(self, channels: list[int] = None, timeout: float = TOUCHLINK_SCAN_TIMEOUT) -> list[TouchLinkDevice]:
        """Scan for TouchLink devices on specified channels."""
        if channels is None:
            channels = [11, 15, 20, 25]  # Primary ZLL channels

        LOGGER.info("Starting TouchLink scan on channels %s", channels)
        devices = []

        async with self._app.interpan_mode():
            for channel in channels:
                await self._app.set_interpan_channel(channel)
                LOGGER.debug("Scanning channel %d", channel)

                channel_devices = await self._scan_channel(channel, timeout / len(channels))
                devices.extend(channel_devices)

        LOGGER.info("TouchLink scan complete, found %d devices", len(devices))
        return devices

    async def _scan_channel(self, channel: int, timeout: float) -> list[TouchLinkDevice]:

        # Generate random transaction ID  
        transaction_id = secrets.randbits(32)  

        # Create scan request packet  
        scan_packet = self._create_scan_packet(transaction_id)  

        discovered_devices = []

        # Send scan request first
        await self._app.send_interpan_packet(scan_packet)

        # Then collect responses with individual wait calls
        end_time = asyncio.get_event_loop().time() + timeout  

        while asyncio.get_event_loop().time() < end_time:
            remaining_time = end_time - asyncio.get_event_loop().time()
            if remaining_time <= 0:
                break

            # Create a NEW response waiter for each attempt
            async with self._app.wait_for_interpan_response([]) as response_future:
                try:
                    hdr, args = await asyncio.wait_for(
                        response_future,
                        timeout=min(remaining_time, 1.0)  # Max 1 second per attempt
                    )

                    # Verify this is a scan response with our transaction ID
                    if (hdr.command_id == LightLink.ClientCommandDefs.scan_rsp.id and  # ✅ Check correct command ID
                        hasattr(args, 'inter_pan_transaction_id') and
                        args.inter_pan_transaction_id == transaction_id):

                        device = TouchLinkDevice(args)
                        discovered_devices.append(device)
                        LOGGER.debug("Found TouchLink device: %r", device)

                except asyncio.TimeoutError:
                    # No more responses, continue to next attempt or exit
                    continue
                except Exception as e:
                    LOGGER.debug("Error processing scan response: %s", e)
                    continue

        return discovered_devices

    def _create_scan_packet(self, transaction_id: int) -> t.ZigbeePacket:
        """Create a TouchLink scan request packet."""

        # Create ZCL header
        hdr = ZCLHeader(
            frame_control=foundation.FrameControl(
                frame_type=foundation.FrameType.CLUSTER_COMMAND,
                is_manufacturer_specific=False,
                direction=foundation.Direction.Client_to_Server,
                disable_default_response=True,
                reserved=0,
            ),
            tsn=secrets.randbits(8),
            command_id=LightLink.ServerCommandDefs.scan.id,
        )

        zigbee_info = ZigbeeInformation(
            logical_type=0,  # Coordinator?
            rx_on_when_idle=1,
            reserved=0
        )

        touchlink_info = ScanRequestInformation(
            factory_new=1,
            address_assignment=1, 
            reserved1=0,
            touchlink_initiator=1,
            undefined=0,
            reserved2=0,
            profile_interop=1,
        )

        scan_cmd = LightLink.ServerCommandDefs.scan.schema(
            inter_pan_transaction_id=transaction_id,
            zigbee_information=zigbee_info,
            touchlink_information=touchlink_info,
        )

        # Serialize command data
        data = hdr.serialize() + scan_cmd.serialize()

        # Create inter-PAN packet
        return t.ZigbeePacket(
            src=t.AddrModeAddress(
                addr_mode=t.AddrMode.IEEE,
                address=self._app.state.node_info.ieee,
            ),
            profile_id=TOUCHLINK_PROFILE_ID,
            cluster_id=LightLink.cluster_id,
            src_ep=1,
            dst_ep=1,
            tsn=hdr.tsn,
            data=t.SerializableBytes(data),
            dst=t.AddrModeAddress(
                addr_mode=t.AddrMode.Broadcast,
                address=t.NWK(0xFFFF),
            ),
            is_interpan=True,
        )

    async def factory_reset(
        self,
        device: TouchLinkDevice,
        timeout: float = TOUCHLINK_FACTORY_RESET_TIMEOUT  
    ) -> bool:
        """Factory reset a TouchLink device."""  

        LOGGER.info("Factory resetting TouchLink device: %r", device)

        async with self._app.interpan_mode():
            # Set channel to device's channel
            await self._app.set_interpan_channel(device.logical_channel)

            # Generate new transaction ID
            transaction_id = secrets.randbits(32)

            # Create factory reset packet
            reset_packet = self._create_factory_reset_packet(transaction_id)  

            # Send factory reset command
            await self._app.send_interpan_packet(reset_packet)

            # Wait a moment for the reset to take effect
            await asyncio.sleep(timeout)

            LOGGER.info("Factory reset command sent to device")
            return True

    def _create_factory_reset_packet(self, transaction_id: int) -> t.ZigbeePacket:  
        """Create a TouchLink factory reset packet."""

        # Create ZCL header
        hdr = ZCLHeader(
            frame_control=foundation.FrameControl(
                frame_type=foundation.FrameType.CLUSTER_COMMAND,
                is_manufacturer_specific=False,
                direction=foundation.Direction.Client_to_Server,
                disable_default_response=True,
                reserved=0,
            ),  
            tsn=secrets.randbits(8),
            command_id=LightLink.ServerCommandDefs.reset_to_factory_new.id,  
        )

        # Create factory reset command
        reset_cmd = LightLink.ServerCommandDefs.reset_to_factory_new.schema(  
            inter_pan_transaction_id=transaction_id,
        )

        # Serialize command data
        data = hdr.serialize() + reset_cmd.serialize()

        # Create inter-PAN packet
        return t.ZigbeePacket(
            src=t.AddrModeAddress(
                addr_mode=t.AddrMode.IEEE,
                address=self._app.state.node_info.ieee,
            ),
            profile_id=TOUCHLINK_PROFILE_ID,
            cluster_id=LightLink.cluster_id,
            src_ep=1,
            dst_ep=1,
            tsn=hdr.tsn,
            data=t.SerializableBytes(data),
            dst=t.AddrModeAddress(
                addr_mode=t.AddrMode.Broadcast,
                address=t.NWK(0xFFFF),
            ),
            is_interpan=True,
        )