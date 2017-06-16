import cProfile
import logging
import os
import time
from abc import ABCMeta, abstractmethod
from enum import Enum

from mpi4py import MPI

import spykshrk.realtime.binary_record as binary_record
from spykshrk.realtime.logging import LoggingClass, PrintableMessage
from spykshrk.realtime.timing_system import TimingMessage


class MPIMessageTag(Enum):
    COMMAND_MESSAGE = 1
    SIMULATOR_DATA = 2
    FEEDBACK_DATA = 3
    TIMING_MESSAGE = 4


class RealtimeMPIClass(LoggingClass):
    def __init__(self, comm: MPI.Comm, rank, config, *args, **kwargs):
        self.comm = comm
        self.rank = rank
        self.config = config
        super(RealtimeMPIClass, self).__init__(*args, **kwargs)


class DataSourceError(RuntimeError):
    pass


class BinaryRecordBaseError(RuntimeError):
    pass


class DataSourceReceiver(RealtimeMPIClass, metaclass=ABCMeta):
    """An abstract class that ranks should use to communicate between neural data sources.

    This class should not be instantiated, only its subclasses.

    This provides an abstraction layer for sources of neural data (e.g., saved file simulator, acquisition system)
    to pipe data (e.g., spikes, lfp, position) to ranks that request data for processing.  This is only an abstraction
    for a streaming data (e.g. sockets, MPI) and makes a number of assumptions:

    1. The type of data and 'channel' (e.g., electrode channels 1, 2, 3) can be streamed to different client processes
    and registered by a client one channel at a time

    2. The streams can be started and stopped arbitrarily after the connection is established (no rule if data is lost
    during pause)

    3. The connection is destroyed when the iterator stops.
    """
    @abstractmethod
    def register_datatype_channel(self, datatype, channel):
        """

        Args:
            datatype: The type of data to request to be streamed, specified by spykshrk.realtime.datatypes.Datatypes
            channel: The channel of the data type to stream

        Returns:
            None

        """
        pass

    @abstractmethod
    def start_all_streams(self):
        pass

    @abstractmethod
    def stop_all_streams(self):
        pass

    @abstractmethod
    def stop_iterator(self):
        pass

    @abstractmethod
    def __next__(self):
        pass


class TimingSystemBase(LoggingClass):

    def __init__(self, *args, **kwds):
        self.time_writer = None
        self.rank = kwds['rank']
        super(TimingSystemBase, self).__init__(*args, **kwds)

    def set_timing_writer(self, time_writer):
        self.time_writer = time_writer

    def write_timing_message(self, timing_msg: TimingMessage):
        if self.time_writer is not None:
            timing_msg.record_time(self.rank)
            self.time_writer.write_timing_message(timing_msg=timing_msg)
        else:
            self.class_log.warning('Tried writing timing message before timing file created.')


class BinaryRecordBase(LoggingClass):
    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)
        self.rank = kwds['rank']
        self.local_rec_manager = kwds['local_rec_manager']
        self.rec_id = kwds['rec_id']
        self.rec_labels = kwds['rec_labels']
        self.rec_struct_fmt = kwds['rec_format']
        self.rec_writer = None    # type: binary_record.BinaryRecordsFileWriter
        self.rec_writer_enabled = False

    def get_record_register_message(self):
        return self.local_rec_manager.create_register_rec_type_message(rec_id=self.rec_id, rec_labels=self.rec_labels,
                                                                       rec_struct_fmt=self.rec_struct_fmt)

    def set_record_writer_from_message(self, create_message):
        self.class_log.info('Creating record from message {}'.format(create_message))
        self.set_record_writer(self.local_rec_manager.create_writer_from_message(create_message, mpi_rank=self.rank))

    def set_record_writer(self, rec_writer):
        self.class_log.info('Setting record writer {}'.format(rec_writer))
        if self.rec_writer_enabled:
            raise BinaryRecordBaseError('Can\'t change writer file while recording is on going!')
        self.rec_writer = rec_writer

    def start_record_writing(self):
        self.class_log.info('Starting record writer.')
        if self.rec_writer:
            if not self.rec_writer.closed:
                self.rec_writer_enabled = True
            else:
                raise BinaryRecordBaseError('Can\'t start recording, file not open!')
        else:
            raise BinaryRecordBaseError('Can\'t start recording, record file never set!')

    def stop_record_writing(self):
        self.rec_writer_enabled = False

    def close_record(self):
        self.stop_record_writing()
        if self.rec_writer:
            self.rec_writer.close()

    def write_record(self, *args):
        if self.rec_writer_enabled and not self.rec_writer.closed:
            self.rec_writer.write_rec(self.rec_id, *args)
            return True
        return False


class ExceptionLoggerWrapperMeta(type):
    """
        A metaclass that wraps the run() or main_loop() method so exceptions are logged.

        This metaclass is built to solve a very specific bug in MPI + threads: a race condition that sometimes
        prevents a thread's uncaught exception from being displayed to stderr before program stalls.

        This class also avoids the known issue with logging exception in threads using sys.excepthook, the hook
        needs to be set by the thread after it is started.
    """
    @staticmethod
    def exception_wrap(func):
        def outer(self):
            try:
                func(self)
            except Exception as ex:
                logging.exception(ex.args)
                # traceback.print_exc(file=sys.stdout)

        return outer

    def __new__(mcs, name, bases, attrs):
        if 'run' in attrs:
            attrs['run'] = mcs.exception_wrap(attrs['run'])
        if 'main_loop' in attrs:
            attrs['main_loop'] = mcs.exception_wrap(attrs['main_loop'])

        return super(ExceptionLoggerWrapperMeta, mcs).__new__(mcs, name, bases, attrs)

    def __init__(cls, name, bases, attrs):
        super(ExceptionLoggerWrapperMeta, cls).__init__(name, bases, attrs)


class ProfilerWrapperMeta(type):
    @staticmethod
    def profile_wrap(func):
        def outer(self):
            if self.enable_profiler:
                prof = cProfile.Profile(timer=time.perf_counter)
                prof.runcall(func, self)
                prof.dump_stats(file=self.profiler_out_path)

        return outer

    def __new__(mcs, name, bases, attrs):
        if 'run' in attrs:
            attrs['run'] = mcs.profile_wrap(attrs['run'])
        if 'main_loop' in attrs:
            attrs['main_loop'] = mcs.profile_wrap(attrs['main_loop'])

        return super(ProfilerWrapperMeta, mcs).__new__(mcs, name, bases, attrs)

    def __init__(cls, name, bases, attrs):
        super(ProfilerWrapperMeta, cls).__init__(name, bases, attrs)


class RealtimeMeta(ExceptionLoggerWrapperMeta, ProfilerWrapperMeta):
    """ A metaclass that combines all coorperative metaclass features needed (wrapping the run/main_loop functions
    with cProfilers and catching unhandled exceptions.
    
    Care needs to be taken that if the meta classes are modifying attributes, each of those modifications is unique.
    This is an issue when chaining the wrapping of functions, the name for the wrapping function needs to be unique,
    e.g. ProfileWrapperMeta and ExceptionLoggerWrapperMeta cannot both use a wrapping function with the same name
    (wrap), they must be unique (exception_wrap and profile_wrap).
    
    """
    def __new__(mcs, name, bases, attrs):
        return super(RealtimeMeta, mcs).__new__(mcs, name, bases, attrs)

    def __init__(cls, name, bases, attrs):
        super(RealtimeMeta, cls).__init__(name, bases, attrs)


class RealtimeProcess(RealtimeMPIClass, metaclass=RealtimeMeta):

    def __init__(self, comm: MPI.Comm, rank, config, **kwds):

        super().__init__(comm=comm, rank=rank, config=config)

        self.enable_profiler = rank in self.config['rank_settings']['enable_profiler']
        self.profiler_out_path = os.path.join(config['files']['output_dir'], '{}.{:02d}.{}'.
                                              format(config['files']['prefix'],
                                                     rank,
                                                     config['files']['profile_postfix']))

    def main_loop(self):
        pass


class DataStreamIterator(LoggingClass, metaclass=ABCMeta):

    @abstractmethod
    def __init__(self):
        super().__init__()
        self.source_infos = {}
        self.source_handlers = []
        self.source_enabled_list = []

    @abstractmethod
    def turn_on_stream(self):
        pass

    @abstractmethod
    def turn_off_stream(self):
        pass

    def set_sources(self, source_infos):
        self.source_infos = source_infos

    def set_enabled(self, source_enable_list):
        self.source_enabled_list = source_enable_list

    @abstractmethod
    def __next__(self):
        pass


class TerminateErrorMessage(PrintableMessage):
    def __init__(self, message):
        self.message = message
        pass


class TerminateMessage(PrintableMessage):
    def __init__(self):
        pass


class EnableStimulationMessage(PrintableMessage):
    def __init__(self):
        pass


class DisableStimulationMessage(PrintableMessage):
    def __init__(self):
        pass


class StartRecordMessage(PrintableMessage):
    def __init__(self):
        pass


class StopRecordMessage(PrintableMessage):
    def __init__(self):
        pass


class CloseRecordMessage(PrintableMessage):
    def __init__(self):
        pass


class RequestStatusMessage(PrintableMessage):
    def __init__(self):
        pass


class ResetFilterMessage(PrintableMessage):
    def __init__(self):
        pass


class NumTrodesMessage(PrintableMessage):
    def __init__(self, num_ntrodes):
        self.num_ntrodes = num_ntrodes


class TurnOnLFPMessage(PrintableMessage):
    def __init__(self, lfp_enable_list):
        self.lfp_enable_list = lfp_enable_list


class TurnOffLFPMessage(PrintableMessage):
    def __init__(self):
        pass
