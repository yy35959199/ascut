def get_h265_nal_unit_type(packet_data: bytes) -> int | None:
    """
    Extract NAL unit type from H.265/HEVC packet data.
    For packets with multiple NAL units, prioritizes picture NAL types (0-21)
    over metadata types (32-40). Returns safe keyframes first (16-20), then
    other picture types, then metadata.
    """
    if not packet_data or len(packet_data) < 6:
        return None

    data_len = len(packet_data)
    nal_length = int.from_bytes(packet_data[:4], byteorder="big")
    if nal_length > 4 and nal_length <= data_len - 4:
        nal_types_found = []
        i = 0
        while i < data_len - 4:
            nal_len = int.from_bytes(packet_data[i : i + 4], byteorder="big")
            if nal_len < 2 or nal_len > data_len - i - 4:
                break
            if i + 5 < data_len:
                nal_type = (packet_data[i + 4] >> 1) & 0x3F
                nal_types_found.append(nal_type)
                if nal_type in (16, 17, 18, 19, 20):
                    return nal_type
            i += 4 + nal_len
        if nal_types_found:
            for nal_type in nal_types_found:
                if nal_type == 21:
                    return nal_type
            for nal_type in nal_types_found:
                if 0 <= nal_type <= 15:
                    return nal_type
            return nal_types_found[0]

    nal_types_found = []
    start_code_4 = b"\x00\x00\x00\x01"
    start_code_3 = b"\x00\x00\x01"
    pos = 0
    while pos < data_len - 5:
        idx4 = packet_data.find(start_code_4, pos)
        idx3 = packet_data.find(start_code_3, pos)
        if idx4 == -1 and idx3 == -1:
            break
        if idx4 != -1 and (idx3 == -1 or idx4 <= idx3):
            if idx4 + 6 <= data_len:
                nal_type = (packet_data[idx4 + 4] >> 1) & 0x3F
                nal_types_found.append(nal_type)
                if nal_type in (16, 17, 18, 19, 20):
                    return nal_type
            pos = idx4 + 4
        else:
            if idx3 + 5 <= data_len:
                nal_type = (packet_data[idx3 + 3] >> 1) & 0x3F
                nal_types_found.append(nal_type)
                if nal_type in (16, 17, 18, 19, 20):
                    return nal_type
            pos = idx3 + 3

    if nal_types_found:
        for nal_type in nal_types_found:
            if nal_type == 21:
                return nal_type
        for nal_type in nal_types_found:
            if 0 <= nal_type <= 15:
                return nal_type
        return nal_types_found[0]
    return None


def is_safe_h264_keyframe_nal(nal_type: int | None) -> bool:
    if nal_type is None:
        return True
    return nal_type in [5, 6, 7, 8]


def is_safe_h265_keyframe_nal(nal_type: int | None) -> bool:
    if nal_type is None:
        return True
    return nal_type in [16, 17, 18, 19, 20, 21, 32, 33, 34]


def is_rasl_nal_type(nal_type: int | None) -> bool:
    if nal_type is None:
        return False
    return nal_type in [8, 9]


def is_radl_nal_type(nal_type: int | None) -> bool:
    if nal_type is None:
        return False
    return nal_type in [6, 7]


def is_leading_picture_nal_type(nal_type: int | None) -> bool:
    return is_rasl_nal_type(nal_type) or is_radl_nal_type(nal_type)


def get_h264_nal_unit_type(packet_data: bytes) -> int | None:
    if not packet_data or len(packet_data) < 5:
        return None

    data_len = len(packet_data)
    nal_length = int.from_bytes(packet_data[:4], byteorder="big")
    if nal_length > 4 and nal_length <= data_len - 4:
        nal_types_found = []
        i = 0
        while i < data_len - 4:
            nal_len = int.from_bytes(packet_data[i : i + 4], byteorder="big")
            if nal_len < 1 or nal_len > data_len - i - 4:
                break
            if i + 4 < data_len:
                nal_type = packet_data[i + 4] & 0x1F
                nal_types_found.append(nal_type)
                if nal_type == 5:
                    return 5
            i += 4 + nal_len
        if nal_types_found:
            for nal_type in nal_types_found:
                if 1 <= nal_type <= 4:
                    return nal_type
            return nal_types_found[0]

    nal_types_found = []
    start_code_4 = b"\x00\x00\x00\x01"
    start_code_3 = b"\x00\x00\x01"
    pos = 0
    while pos < data_len - 4:
        idx4 = packet_data.find(start_code_4, pos)
        idx3 = packet_data.find(start_code_3, pos)
        if idx4 == -1 and idx3 == -1:
            break
        if idx4 != -1 and (idx3 == -1 or idx4 <= idx3):
            if idx4 + 5 <= data_len:
                nal_type = packet_data[idx4 + 4] & 0x1F
                nal_types_found.append(nal_type)
                if nal_type == 5:
                    return 5
            pos = idx4 + 4
        else:
            if idx3 + 4 <= data_len:
                nal_type = packet_data[idx3 + 3] & 0x1F
                nal_types_found.append(nal_type)
                if nal_type == 5:
                    return 5
            pos = idx3 + 3
    if nal_types_found:
        for nal_type in nal_types_found:
            if 1 <= nal_type <= 4:
                return nal_type
        return nal_types_found[0]
    return None

