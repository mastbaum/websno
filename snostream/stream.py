'''Data input interface

Data sources run in separate threading.Threads and push their data via a
user-provided callback.
'''

import threading

class InputStream(threading.Thread):
    '''An objects which receives data and passes it along via a callback'''
    def __init__(self, callback):
        threading.Thread.__init__(self)
        self.callback = callback

    def run(self):
        raise Exception('Cannot call run on InputStream base class')


class EventSource(InputStream):
    '''An InputStream that provides event-level data, for use in an event
    display. Sends Event objects rather than JSON data.'''
    class EventUnavailable(Exception):
        '''Exception raised when an EventSource is unable to provide a requested
        event.
        '''
        def __init__(self, message):
            self.message = message
        def __str__(self):
            return repr(self.message)


class EventPickleFile(EventSource):
    '''Special event format for websnoed testing'''
    def __init__(self, filename, callback, interval=2):
        InputStream.__init__(self, callback)

        self.interval = interval
        self.filename = filename
        self.idx = 0

        import pickle
        with open(self.filename) as f:
            self.events = pickle.load(f)

    def run(self):
        '''ship events on a fixed timer'''
        import time
        for ev in self.events:
            ev['source'] = 'Event Pickle: %s' % self.filename
            self.callback(self.idx, ev)
            self.idx += 1
            time.sleep(self.interval)

    def get(self, idx):
        '''get an event by index'''
        try:
            return self.events[idx]
        except IndexError:
            raise EventSource.EventUnavailable('Event %i unavailable in %s' % (idx, self.filename))


class RATRootFile(EventSource):
    '''Read events and records from a RAT ROOT file'''
    def __init__(self, filename, callback, interval=2):
        InputStream.__init__(self, callback)

        self.filename = filename
        self.interval = interval
        self.idx = 0

        from rat import ROOT
        self.t = ROOT.TChain("T")
        self.t.Add(filename)

        self.ds = ROOT.RAT.DS.Root()
        self.t.SetBranchAddress("ds", self.ds)

    @classmethod
    def ev_to_dict(self, ev, source=''):
        '''convert a RAT::DS::EV to a dictionary websnoed can handle'''
        d = {
            'gtid': hex(ev.GetEventID()),
            'nhit': ev.GetNhits(),
            'trig': ev.GetTrigType(),
            'source': source
        }

        pmtid = []
        qhs = []
        t = []
        for i in range(ev.GetPMTUnCalCount()):
            pmt = ev.GetPMTUnCal(i)
            pmtid.append(pmt.GetID())
            qhs.append(pmt.GetsQHS())
            t.append(pmt.GetsPMTt())

        d['q'] = qhs
        d['t'] = t
        d['id'] = pmtid

        # try to make histograms with numpy
        try:
            import numpy
            d['qhist'] = map((lambda x: map(float, x)), zip(*reversed(numpy.histogram(d['q'], bins=50))))
            d['thist'] = map((lambda x: map(float, x)), zip(*reversed(numpy.histogram(d['t'], bins=50))))
        except ImportError:
            pass

        return d

    def run(self):
        '''ship events on a fixed timer'''
        import time
        for i in range(self.t.GetEntries()):
            self.t.GetEntry(i)
            d = self.ev_to_dict(self.ds.GetEV(0), 'RAT File: %s' % self.filename)
            self.callback(i, d) 
            self.idx = i
            time.sleep(self.interval)

    def get(self, idx):
        '''get an event by index'''
        if idx > 0 and idx < self.t.GetEntries():
            self.t.GetEntry(idx)
            return self.ev_to_dict(self.ds.GetEV(0))
        else:
            raise EventSource.EventUnavailable('Event %i unavailable in %s' % (idx, self.filename))


class ZDABFile(EventSource):
    '''Read events and records from a ZDAB file'''
    def __init__(self, filename, callback, interval=2):
        InputStream.__init__(self, callback)

        self.filename = filename
        self.interval = interval
        self.idx = 0

        import ratzdab
        self.zdabfile = ratzdab.zdabfile(filename)
        
    def run(self):
        '''ship events on a fixed timer'''
        import time
        import ratzdab

        while True:
            try:
                o = self.zdabfile.next()

                if o == None:
                    break

                if o.IsA() == ratzdab.ROOT.RAT.DS.Root.Class():
                    d = RATRootFile.ev_to_dict(o.GetEV(0), 'ZDAB File: %s' % self.filename)
                    self.callback(o.GetEV(0).GetEventID(), d)
                    self.idx += 1
                    time.sleep(self.interval)

                del o

            except Exception: # fixme: it's a ratzdab::unknown_record_error
                print 'exception: could not unpack record'
                continue


class ZDABDispatch(EventSource):
    '''Receive events and records from a ZDAB dispatch stream'''
    def __init__(self, hostname, callback):
        InputStream.__init__(self, callback)

        self.hostname = hostname

        import ratzdab
        self.dispatcher = ratzdab.dispatch(hostname)
        
    def run(self):
        '''ship events as they arrive from the zdab dispatch'''
        import ratzdab

        while True:
            try:
                o = self.dispatch.next()

                if o.IsA() == ratzdab.ROOT.RAT.DS.Root.Class():
                    d = RATRootFile.ev_to_dict(o.GetEV(0), 'ZDAB Dispatch: %s' % self.hostname)
                    self.callback(o.GetEV(0).GetEventID(), d)

            except Exception: # fixme: it's a ratzdab::unknown_record_error
                continue


class OrcaRootStream(InputStream):
    '''Read and JSONize objects from a normal OrcaRoot stream'''
    def __init__(self, callback):
        InputStream.__init__(self, callback)


class OrcaJSONStream(InputStream):
    '''Read documents from a ZeroMQ OrcaRoot JSON stream'''
    def __init__(self, callback):
        import zmq
        InputStream.__init__(self, callback)


class CouchDBChanges(InputStream):
    '''Read changes from a CouchDB database'''
    def __init__(self, host, dbname, callback, username=None, password=None):
        InputStream.__init__(self, callback)
        import couchdb

        couch = couchdb.Server(host)

        if username and password:
            couch.resource.credentials = (username, password)

        self.db = couch[dbname]

    def run(self):
        for change in self.db.changes(include_docs=True, heartbeat=50000, feed='continuous'):
            doc = change['doc']
            self.callback(doc)
