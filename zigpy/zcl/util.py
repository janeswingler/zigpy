
import logging
from typing import Type, Optional, Dict, Any
from zigpy.zcl import foundation, clusters
LOGGER = logging.getLogger(__name__)


# We have to figure out where to parse the ZigbeePacket given to register_packet_callback()
# Whose job is it to get stuff out of the zigbee packet like profile_id and cluster_id? Is that notify_packet_callbacks in application.py?
# Should callbacks already receive parsed ZCL data?


# I want examples of a variety of callbacks (touchlink included) to see example workflows
# What kinds of packets can we reasonably expect to parse in these utilities? ZDO? ZCL general?

def parse_zcl_frame(data: bytes, cluster_id: Optional[int] = None) -> Dict[str, Any]:
    """Parse ZCL frame data into header and payload."""
    try:
        zcl_hdr, remaining_data = foundation.ZCLHeader.deserialize(data)
        command_id = zcl_hdr.command_id
        frame_control = zcl_hdr.frame_control

        # Look up cluster definition if provided
        cluster_class = get_cluster_class(cluster_id) if cluster_id else None
        command_def = None
        payload = None

        if cluster_class:
            if frame_control.direction == foundation.Direction.Client_to_Server:
                command_def = cluster_class.server_commands.get(command_id)
            else:
                command_def = cluster_class.client_commands.get(command_id)
                

            # Parse payload using command_def if available
            if command_def:
                try:
                    parsed_payload, _ = command_def.schema.deserialize(remaining_data)
                except Exception as e:
                    LOGGER.debug("Failed to parse command payload: %s", e)
                    parsed_payload = remaining_data
            else:
                parsed_payload = remaining_data
        else:
            parsed_payload = remaining_data

        return {
            "frame_control": frame_control,
            "tsn": zcl_hdr.tsn,
            "command_id": command_id,
            "command_def": command_def,
            "payload": parsed_payload,
            "cluster_id": cluster_id,
            "manufacturer": zcl_hdr.manufacturer,
        }
        
    except Exception as e:
        LOGGER.exception("Error parsing ZCL frame")
        return {
            "error": str(e),
            "raw_data": data
        }


def get_cluster_class(cluster_id: int) -> Optional[type]:
    """Return the cluster class for a given cluster_id, or None if not found."""
    return clusters.CLUSTERS_BY_ID.get(cluster_id)


def test_parse_zcl_frame():
    # Build a ZCL frame: frame_control=0x01 (cluster-specific), tsn=0x42, command_id=0x05
    zcl_hdr = foundation.ZCLHeader(
        frame_control=0x01,
        tsn=0x42,
        command_id=0x05,
        manufacturer=None,
    )
    hdr_bytes = zcl_hdr.serialize()
    payload_bytes = b"\x11\x22\x33"  # Example payload
    frame = hdr_bytes + payload_bytes

    # Use a known cluster_id (e.g., 0x0006 OnOff cluster)
    cluster_id = 0x0006
    result = parse_zcl_frame(frame, cluster_id=cluster_id)
    print("Parsed ZCL frame:", result)
    assert result["frame_control"] == 0x01
    assert result["tsn"] == 0x42
    assert result["command_id"] == 0x05
    assert result["payload"] == payload_bytes or isinstance(result["payload"], bytes)
    print("ZCL frame test passed!")

if __name__ == "__main__":
    test_parse_zcl_frame()