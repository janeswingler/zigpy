import asyncio
import logging
import zigpy.zcl.util as zcl_util
from typing import Dict, List, Optional, Callable, Any
import zigpy.types as t
from zigpy.zcl import foundation
from zigpy.zcl.clusters.lightlink import LightLink, ZigbeeInformation, ScanRequestInformation

LOGGER = logging.getLogger(__name__)

DEFAULT_SCAN_RESPONSE_WINDOW_S = 1.0  # wait time per channel for scan responses
ZLL_PROFILE_ID = 0xC05E  # TouchLink / ZLL profile
LIGHTLINK_CLUSTER_ID = LightLink.cluster_id  # 0x1000

class TouchLinkManager:
    """Manages TouchLink operations using packet callbacks."""
    
    def __init__(self, app):
        self.app = app
        self._scan_responses = []
        self._transaction_id = 0

    def get_transaction_id(self) -> int:
        """Get next transaction ID."""
        self._transaction_id = (self._transaction_id +1) % (0xFFFFFFFF)
        return self._transaction_id
    
    async def scan_for_devices(self, channels: List[int]) -> List[Dict[str, Any]]:
        """Scan for TouchLink devices on specified channels."""
        results = []
        
        for channel in channels:
            LOGGER.debug(f"Scanning channel {channel}")
            channel_results = await self._scan_channel(channel)
            results.extend(channel_results)

        return results
    
    async def _scan_channel(self, channel: int) -> List[Dict[str, Any]]:
        """Scan a single channel for TouchLink devices."""
        
        responses = []

        def scan_response_callback(packet: t.ZigbeePacket):
            if (packet.profile_id == ZLL_PROFILE_ID and 
                packet.cluster_id == LightLink.cluster_id):

                parsed = zcl_util.parse_zcl_frame(packet.data, LightLink.cluster_id)

                if (parsed.get("command_def") and 
                    hasattr(parsed["command_def"], "name") and
                    parsed["command_def"].name == "scan_rsp"):
                    responses.append({
                        'src': packet.src,
                        'channel': channel,
                        'data': parsed,
                        'lqi': getattr(packet, 'lqi', None),
                        'rssi': getattr(packet, 'rssi', None),
                    })
        
        # Register callback for TouchLink scan responses
        cancel_callback = self.app.register_packet_callback(None, scan_response_callback)

        try:
            # Send scan request
            await self._send_scan_request(channel)

            # Wait for responses
            await asyncio.sleep(DEFAULT_SCAN_RESPONSE_WINDOW_S)

            return responses
        finally:
            # Unregister callback
            cancel_callback()

    async def _send_scan_request(self, channel: int):
        """Send a TouchLink scan request on a specified channel"""
        
        # Enter interpan mode
        async with self.app.inter_pan_mode():
            await self.app.set_interpan_channel(channel)

            # Build scan request packet
            packet = self._build_scan_request_packet()
            await self.app.send_interpan(packet)

    def _build_scan_request_packet(self) -> t.ZigbeePacket:
        """Build a TouchLink scan request packet."""
        
        # Build ZCL header
        zcl_hdr = foundation.ZCLHeader(
            frame_control=foundation.FrameControl(
                frame_type=foundation.FrameType.CLUSTER_COMMAND,
                is_manufacturer_specific=False,
                direction=foundation.Direction.Client_to_Server,
                disable_default_response=True,
            ),
            tsn=self.app.get_sequence(),
            command_id=0x00,  # scan command
        )

            # Build command payload using the schema
        scan_cmd = LightLink.ServerCommandDefs.scan
        payload_data = {
            "inter_pan_transaction_id": self.get_transaction_id(),
            "zigbee_information": ZigbeeInformation(
                logical_type=0,  # Coordinator
                rx_on_when_idle=1,
                reserved=0
            ),
            "touchlink_information": ScanRequestInformation(
                factory_new=0,
                address_assignment=1,
                reserved1=0,
                touchlink_initiator=1,
                undefined=0,
                reserved2=0,
                profile_interop=1
            )
        }

        payload = scan_cmd.schema.serialize(payload_data)
        frame_data = zcl_hdr.serialize() + payload
        
        return t.ZigbeePacket(
            src=t.AddrModeAddress(
                addr_mode=t.AddrMode.NWK, 
                address=self.app.state.node_info.nwk
            ),
            src_ep=1,
            dst=t.AddrModeAddress(
                addr_mode=t.AddrMode.Broadcast,
                address=t.BroadcastAddress.ALL_DEVICES
            ),
            dst_ep=1,
            tsn=zcl_hdr.tsn,
            profile_id=ZLL_PROFILE_ID,
            cluster_id=LightLink.cluster_id,
            data=t.SerializableBytes(frame_data),
            is_interpan=True,
            channel=None,  # Will be set by send_interpan_packet
        )

