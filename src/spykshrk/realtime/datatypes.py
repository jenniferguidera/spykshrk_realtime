
from enum import IntEnum
import struct
from spykshrk.realtime.realtime_logging import PrintableMessage
from mpi4py import MPI


class Datatypes(IntEnum):
    LFP = 1
    SPIKES = 2
    POSITION = 3
    LINEAR_POSITION = 4


class SpikePoint(PrintableMessage):
    """
    Spike data message.
    """
    _byte_format = '=qi40h40h40h40h'

    def __init__(self, timestamp, ntrode_id, data):
        self.timestamp = timestamp
        self.ntrode_id = ntrode_id
        self.data = data

    def pack(self):
        return struct.pack(self._byte_format, self.timestamp, self.ntrode_id,
                           *self.data[0], *self.data[1], *self.data[2], *self.data[3])

    @classmethod
    def unpack(cls, message_bytes):
        timestamp, ntrode_id, *raw_data = struct.unpack(cls._byte_format, message_bytes)
        return cls(timestamp=timestamp, ntrode_id=ntrode_id, data=[raw_data[0:40],
                                                                   raw_data[40:80],
                                                                   raw_data[80:120],
                                                                   raw_data[120:160]])

    @classmethod
    def packed_message_size(cls):
        return struct.calcsize(cls._byte_format)


class LFPPoint(PrintableMessage):
    _byte_format = '=qiii'

    def __init__(self, timestamp, ntrode_index, ntrode_id, data):
        self.timestamp = timestamp
        self.ntrode_index = ntrode_index
        self.ntrode_id = ntrode_id
        self.data = data

    def pack(self):
        return struct.pack(self._byte_format, self.timestamp, self.ntrode_index, self.ntrode_id, self.data)

    @classmethod
    def unpack(cls, message_bytes):
        timestamp, ntrode_index, ntrode_id, data = struct.unpack(cls._byte_format, message_bytes)
        message = cls(timestamp=timestamp, ntrode_index=ntrode_index, ntrode_id=ntrode_id, data=data)
        return message

    @classmethod
    def packed_message_size(cls):
        return struct.calcsize(cls._byte_format)


class LinearPosPoint(PrintableMessage):
    _byte_format = '=qff'

    def __init__(self, timestamp, x, vel):
        self.timestamp = timestamp
        self.x = x
        self.vel = vel

    def pack(self):
        return struct.pack(self._byte_format, self.timestamp, self.x, self.vel)

    @classmethod
    def unpack(cls, message_bytes):
        timestamp, x, vel = struct.unpack(cls._byte_format, message_bytes)
        message = cls(timestamp=timestamp, x=x, vel=vel)
        return message

    @classmethod
    def packed_message_size(cls):
        return struct.calcsize(cls._byte_format)

class RawPosPoint(PrintableMessage):
    def __init__(self, timestamp, x1, y1, x2, y2, camera_id):
        self.timestamp = timestamp
        self.x1 = x1
        self.y1 = y1
        self.x2 = x2
        self.y2 = y2
        self.camera_id = camera_id


class PosPoint(PrintableMessage):
    def __init__(self, timestamp, x, y, camera_id):
        self.timestamp = timestamp
        self.x = x
        self.y = y
        self.camera_id = camera_id


class DigIOStateChange(PrintableMessage):
    def __init__(self, timestamp, port, io_dir, state):
        self.timestamp = timestamp
        self.port = port
        self.io_dir = io_dir        # 1 - input, 0 - output
        self.state = state


class SystemTimePoint(PrintableMessage):
    def __init__(self, timestamp, tv_sec, tv_nsec):
        self.timestamp = timestamp
        self.tv_sec = tv_sec
        self.tv_nsec = tv_nsec

