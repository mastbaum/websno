'''ORCA JSON worker

Separate process to crunch the ZeroMQ ORCA JSON stream. Each packet type is processed
in a dedicated greelet pool. The pool is the tool to control resources, and spawns
a greenlet for each packet. Gevent queues are used to distribute packets to pools,
and collect the processed results. A ZeroMQ ipc socket returns the processed packet
to stream.py as a ready-to-store list of dictionaries.
'''

import multiprocessing
import gevent
import gevent.pool
from zmq import green as zmq

class ProcessorPool(object):
    def __init__(self, num, proc, rqueue):
        self.pool = gevent.pool.Pool(num)
        self.proc = proc
        self.rqueue = rqueue

    def add_job(self, o):
        if not self.pool.full():
            self.pool.start(self.proc(o, self.rqueue))

    def terminate(self):
        self.pool.kill()


class _OrcaJSONWorker(multiprocessing.Process):
    def __init__(self):
        multiprocessing.Process.__init__(self)
        self.daemon = True

    def respond(self):
        self._rsocket = self._context.socket(zmq.PUSH)
        self._rsocket.connect('ipc:///tmp/snostream_orcajson_output')

        self.running = True
        while self.running:
            o = self.rqueue.get()
            self._rsocket.send_pyobj(o, zmq.NOBLOCK)

    def run(self):
        from websno.inputstreams import orcajson
        self._context = zmq.Context()
        self._ssocket = self._context.socket(zmq.PULL)
        self._ssocket.bind('ipc:///tmp/snostream_orcajson_input')

        self.rqueue = gevent.queue.Queue()
        gevent.spawn(self.respond)

        pmt_base_current_pool = ProcessorPool(40, orcajson.PmtBaseCurrent, self.rqueue.put)
        hv_status_pool = ProcessorPool(20, orcajson.HVStatus, self.rqueue.put)
        xl3_voltages_pool = ProcessorPool(20, orcajson.XL3Voltages, self.rqueue.put)
        fec_voltages_pool = ProcessorPool(20*16, orcajson.FECVoltages, self.rqueue.put)
        fifo_state_pool = ProcessorPool(20, orcajson.FifoState, self.rqueue.put)
        cmos_count_pool = ProcessorPool(80, orcajson.CmosCount, self.rqueue.put)

        poller = zmq.Poller()
        poller.register(self._ssocket, zmq.POLLIN)

        def missing(o):
            print 'orcajson processor missing for packet type: ' + o['type']

        route = {
            'pmt_base_current': pmt_base_current_pool.add_job,
            'xl3_hv': hv_status_pool.add_job,
            'xl3_vlt': xl3_voltages_pool.add_job,
            'fec_vlt': fec_voltages_pool.add_job,
            'fifo_state': fifo_state_pool.add_job,
            'cmos_counts': cmos_count_pool.add_job
        }

        while True:
            socks = dict(poller.poll(100)) 
            if self._ssocket in socks and socks[self._ssocket] == zmq.POLLIN:
                o = self._ssocket.recv_pyobj()
                if 'type' in o:
                    route.get(o['type'], missing)(o)

