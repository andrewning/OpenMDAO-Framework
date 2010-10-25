import os.path
import Queue
import sys
import threading

from enthought.traits.api import Bool, Instance

from openmdao.main.api import Component, Driver
from openmdao.main.exceptions import RunStopped
from openmdao.main.interfaces import ICaseIterator, ICaseRecorder
from openmdao.main.rbac import get_credentials, set_credentials
from openmdao.main.resource import ResourceAllocationManager as RAM
from openmdao.lib.datatypes.int import Int
from openmdao.util.filexfer import filexfer

_EMPTY    = 'empty'
_READY    = 'ready'
_COMPLETE = 'complete'
_ERROR    = 'error'

class _ServerError(Exception):
    """ Raised when a server thread has problems. """
    pass


class CaseIterDriverBase(Driver):
    """
    A base class for Drivers that run sets of cases in a manner similar
    to the ROSE framework. Concurrent evaluation is supported, with the various
    evaluations executed across servers obtained from the
    :class:`ResourceAllocationManager`.
    """

    recorder = Instance(ICaseRecorder, allow_none=True, 
                        desc='Something to save Cases to.')
    
    sequential = Bool(True, iotype='in',
                      desc='If True, evaluate cases sequentially.')

    reload_model = Bool(True, iotype='in',
                        desc='If True, reload the model between executions.')

    max_retries = Int(1, low=0, iotype='in',
                      desc='Maximum number of times to retry a failed case.')

    def __init__(self, *args, **kwargs):
        super(CaseIterDriverBase, self).__init__(*args, **kwargs)

        self._iter = None  # Set to None when iterator is empty.
        self._replicants = 0

        self._egg_file = None
        self._egg_required_distributions = None
        self._egg_orphan_modules = None

        self._reply_q = None  # Replies from server threads.
        self._server_lock = None  # Lock for server data.

        # Various per-server data keyed by server name.
        self._servers = {}
        self._top_levels = {}
        self._server_info = {}
        self._queues = {}
        self._in_use = {}
        self._server_states = {}
        self._server_cases = {}
        self._exceptions = {}

        self._todo = []   # Cases grabbed during server startup.
        self._rerun = []  # Cases that failed and should be retried.

    def execute(self):
        """ Runs all cases and records results in `recorder`. """
        self.setup()
        self.resume()

    def resume(self, remove_egg=True):
        """
        Resume execution.

        remove_egg: bool
            If True, then the egg file created for concurrent evaluation is
            removed at the end of the run.
        """
        self._stop = False
        if self._iter is None:
            self.raise_exception('Run already complete', RuntimeError)

        try:
            if self.sequential:
                self._logger.info('Start sequential evaluation.')
                while self._iter is not None:
                    if self._stop:
                        break
                    try:
                        self.step()
                    except StopIteration:
                        break
            else:
                self._logger.info('Start concurrent evaluation.')
                self._start()
        finally:
            self._cleanup(remove_egg)

        if self._stop:
            self.raise_exception('Run stopped', RunStopped)

    def step(self):
        """ Evaluate the next case. """
        self._stop = False
        if self._iter is None:
            self.setup()

        try:
            self._todo.append(self._iter.next())
        except StopIteration:
            if not self._rerun:
                self._iter = None
                raise

        self._server_cases[None] = None
        self._server_states[None] = _EMPTY
        while self._server_ready(None, stepping=True):
            pass

    def stop(self):
        """ Stop evaluating cases. """
        # Necessary to avoid default driver handling of stop signal.
        self._stop = True

    def setup(self, replicate=True):
        """
        Setup to begin new run.

        replicate: bool
             If True, then replicate the model and save to an egg file
             first (for concurrent evaluation).
        """
        self._cleanup(remove_egg=replicate)

        if not self.sequential:
            if replicate or self._egg_file is None:
                # Save model to egg.
                # Must do this before creating any locks or queues.
                self._replicants += 1
                version = 'replicant.%d' % (self._replicants)
                driver = self.parent.driver
                self.parent.add('driver', Driver()) # this driver will execute the workflow once
                self.parent.driver.workflow = self.workflow
                try:
                    #egg_info = self.model.save_to_egg(self.model.name, version)
                    # FIXME: what name should we give to the egg?
                    egg_info = self.parent.save_to_egg(self.name, version)
                finally:
                    self.parent.driver = driver
                self._egg_file = egg_info[0]
                self._egg_required_distributions = egg_info[1]
                self._egg_orphan_modules = [name for name, path in egg_info[2]]

        self._iter = self.get_case_iterator()
        
    def get_case_iterator(self):
        """Returns a new iterator over the Case set."""
        raise NotImplemented('get_case_iterator')

    def _start(self):
        """ Start evaluating cases concurrently. """
        credentials = get_credentials()

        # Determine maximum number of servers available.
        resources = {
            'required_distributions':self._egg_required_distributions,
            'orphan_modules':self._egg_orphan_modules,
            'python_version':sys.version[:3]}
        max_servers = RAM.max_servers(resources)
        self._logger.debug('max_servers %d', max_servers)
        if max_servers <= 0:
            msg = 'No servers supporting required resources %s' % resources
            self.raise_exception(msg, RuntimeError)

        # Kick off initial wave of cases.
        self._server_lock = threading.Lock()
        self._reply_q = Queue.Queue()
        n_servers = 0
        while n_servers < max_servers:
            if self._stop:
                break
            if self._iter is None:
                break

            # Get next case. Limits servers started if max_servers > cases.
            try:
                self._todo.append(self._iter.next())
            except StopIteration:
                if not self._rerun:
                    self._iter = None
                    break

            # Start server worker thread.
            n_servers += 1
            name = '%s_%d' % (self.name, n_servers)
            self._logger.debug('starting worker for %s', name)
            self._servers[name] = None
            self._in_use[name] = True
            self._server_cases[name] = None
            self._server_states[name] = _EMPTY
            server_thread = threading.Thread(target=self._service_loop,
                                             args=(name, resources,
                                                   credentials, self._reply_q))
            server_thread.daemon = True
            server_thread.start()

            if sys.platform != 'win32':
                # Process any pending events.
                while self._busy():
                    try:
                        name, result, exc = self._reply_q.get(True, 0.1)
                    except Queue.Empty:
                        break  # Timeout.
                    else:
                        if self._servers[name] is None:
                            self._logger.debug('server startup failed for %s', name)
                            self._in_use[name] = False
                        else:
                            self._in_use[name] = self._server_ready(name)

        if sys.platform == 'win32':  #pragma no cover
            # Don't start server processing until all servers are started,
            # otherwise we have egg removal issues.
            for name in self._in_use.keys():
                name, result, exc = self._reply_q.get()
                if self._servers[name] is None:
                    self._logger.debug('server startup failed for %s', name)
                    self._in_use[name] = False

            # Kick-off started servers.
            for name in self._in_use.keys():
                if self._in_use[name]:
                    self._in_use[name] = self._server_ready(name)

        # Continue until no servers are busy.
        while self._busy():
            if not self._todo and not self._rerun and self._iter is None:
                # Don't wait indefinitely for a server we don't need.
                # This has happened with a server that got 'lost'
                # in RAM.allocate()
                timeout = 30
            else:
                timeout = None
            try:
                name, result, exc = self._reply_q.get(timeout=timeout)
            # Hard to force worker to hang, which is handled here.
            except Queue.Empty:  #pragma no cover
                self._logger.error('Timeout waiting with nothing left to do:')
                for name, in_use in self._in_use.items():
                    if in_use:
                        try:
                            server = self._servers[name]
                            info = self._server_info[name]
                        except KeyError:
                            self._logger.error('    %s: no startup reply', name)
                        else:
                            self._logger.error('    %s: %s %s', name,
                                               self._servers[name],
                                               self._server_info[name])
            else:
                self._in_use[name] = self._server_ready(name)

        # Shut-down (started) servers.
        self._logger.critical('Shut-down (started) servers')
        for queue in self._queues.values():
            queue.put(None)
        for i in range(len(self._queues)):
            try:
                name, status, exc = self._reply_q.get(True, 1)
            # Hard to force worker to hang, which is handled here.
            except Queue.Empty:  #pragma no cover
                pass
            else:
                del self._queues[name]
        # Hard to force worker to hang, which is handled here.
        for name in self._queues.keys():  #pragma no cover
            self._logger.warning('Timeout waiting for %s to shut-down.', name)

    def _busy(self):
        """ Return True while at least one server is in use. """
        return any(self._in_use.values())

    def _cleanup(self, remove_egg=True):
        """ Cleanup egg file if necessary. """
        self._reply_q = None
        self._server_lock = None

        self._servers = {}
        self._top_levels = {}
        self._server_info = {}
        self._queues = {}
        self._in_use = {}
        self._server_states = {}
        self._server_cases = {}
        self._exceptions = {}

        self._todo = []
        self._rerun = []

        if self._egg_file and os.path.exists(self._egg_file):
            os.remove(self._egg_file)
            self._egg_file = None

    def _server_ready(self, server, stepping=False):
        """
        Responds to asynchronous callbacks during :meth:`execute` to run cases
        retrieved from `self._iter`.  Results are processed by `recorder`.
        If `stepping`, then we don't grab any new cases.
        Returns True if this server is still in use.
        """
        state = self._server_states[server]
        self._logger.debug('server %s state %s', server, state)
        in_use = True

        if state == _EMPTY:
            if not self._todo and not self._rerun and self._iter is None:
                self._logger.debug('    no more cases')
                in_use = False
            else:
                try:
                    self._logger.debug('    load_model')
                    self._load_model(server)
                    self._server_states[server] = _READY
                except _ServerError:
                    self._server_states[server] = _ERROR

        elif state == _READY:
            # Test for stop request.
            if self._stop:
                self._logger.debug('    stop requested')
                in_use = False

            # Select case to run.
            elif self._todo:
                self._logger.debug('    run startup case')
                self._run_case(self._todo.pop(0), server)
            elif self._rerun:
                self._logger.debug('    rerun case')
                self._run_case(self._rerun.pop(0), server, rerun=True)
            elif self._iter is None:
                self._logger.debug('    no more cases')
                in_use = False
            elif stepping:
                in_use = False
            else:
                try:
                    case = self._iter.next()
                except StopIteration:
                    self._logger.debug('    no more cases')
                    in_use = False
                    self._iter = None
                else:
                    self._logger.debug('    run next case')
                    self._run_case(case, server)
        
        elif state == _COMPLETE:
            case = self._server_cases[server]
            self._server_cases[server] = None
            try:
                exc = self._model_status(server)
                if exc is None:
                    # Grab the data from the model.
                    for i, niv in enumerate(case.outputs):
                        try:
                            case.outputs[i] = (niv[0], niv[1],
                                self._model_get(server, niv[0], niv[1]))
                        except Exception as exc:
                            msg = "Exception getting '%s': %s" % (niv[0], exc)
                            case.msg = '%s: %s' % (self.get_pathname(), msg)
                else:
                    self._logger.debug('    exception %s', exc)
                    case.msg = str(exc)
                # Record the data.
                if self.recorder is not None:
                    self.recorder.record(case)

                if not case.msg:
                    if self.reload_model:
                        self._logger.debug('    reload')
                        self._load_model(server)
                else:
                    self._logger.debug('    load')
                    self._load_model(server)
                self._server_states[server] = _READY
            except _ServerError:
                # Handle server error separately.
                self._logger.debug('    server error')

        elif state == _ERROR:
            self._server_cases[server] = None
            try:
                self._load_model(server)
            except _ServerError:
                pass  # Needs work!
            else:
                self._server_states[server] = _READY

        # Just being defensive, should never happen.
        else:  #pragma no cover
            self._logger.error('unexpected state %s for server %s',
                               state, server)
            in_use = False

        return in_use

    def _run_case(self, case, server, rerun=False):
        """ Setup and run a case. """
        if not rerun:
            if not case.max_retries:
                case.max_retries = self.max_retries
            case.retries = 0

        case.msg = None
        self._server_cases[server] = case

        try:
            for event in self.get_events(): 
                try: 
                    self._model_set(server, event, None, True)
                except Exception as exc:
                    msg = "Exception setting '%s': %s" % (name, exc)
                    self.raise_exception(msg, _ServerError)
            for name, index, value in case.inputs:
                try:
                    self._model_set(server, name, index, value)
                except Exception as exc:
                    msg = "Exception setting '%s': %s" % (name, exc)
                    self.raise_exception(msg, _ServerError)
            self._model_execute(server)
            self._server_states[server] = _COMPLETE
        except _ServerError as exc:
            self._server_states[server] = _ERROR
            if case.retries < case.max_retries:
                case.retries += 1
                self._rerun.append(case)
            else:
                case.msg = str(exc)
                if self.recorder is not None:
                    self.recorder.record(case)

    def _service_loop(self, name, resource_desc, credentials, reply_q):
        """ Each server has an associated thread executing this. """
        set_credentials(credentials)

        server, server_info = RAM.allocate(resource_desc)
        # Just being defensive, this should never happen.
        if server is None:  #pragma no cover
            self._logger.error('Server allocation for %s failed :-(', name)
            self._reply_q.put((name, False, None))
            return
        else:
            # Clear egg re-use indicator.
            server_info['egg_file'] = None

        request_q = Queue.Queue()

        try:
            with self._server_lock:
                self._servers[name] = server
                self._server_info[name] = server_info
                self._queues[name] = request_q

            reply_q.put((name, True, None))  # ACK startup.

            while True:
                request = request_q.get()
                if request is None:
                    reply_q.put((name, True, None))  # ACK shutdown.
                    break
                req_exc = None
                try:
                    result = request[0](request[1])
                except Exception as req_exc:
                    self._logger.error('%s: %s caused %s', request[0], req_exc)
                reply_q.put((name, result, req_exc))
        except Exception as exc:  # pragma no cover
            # This can easily happen if we take a long time to allocate and
            # we get 'cleaned-up' before we get started.
            self._logger.error('%s: %s', exc)
        finally:
            self._logger.debug('%s releasing server', name)
            RAM.release(server)
            del server

    def _load_model(self, server):
        """ Load a model into a server. """
        if server is not None:
            self._queues[server].put((self._remote_load_model, server))
        return True

    def _remote_load_model(self, server):
        """ Load model into remote server. """
        egg_file = self._server_info[server].get('egg_file', None)
        if egg_file is not self._egg_file:
            # Only transfer if changed.
            filexfer(None, self._egg_file,
                     self._servers[server], self._egg_file, 'b')
            self._server_info[server]['egg_file'] = self._egg_file
        tlo = self._servers[server].load_model(self._egg_file)
        if not tlo:
            self._logger.error("server.load_model of '%s' failed :-(",
                               self._egg_file)
            return False
        self._top_levels[server] = tlo
        return True

    def _model_set(self, server, name, index, value):
        """ Set value in server's model. """
        if server is None:
            self.parent.set(name, value, index)
        else:
            self._top_levels[server].set(name, value, index)

    def _model_get(self, server, name, index):
        """ Get value from server's model. """
        if server is None:
            return self.parent.get(name, index)
        else:
            return self._top_levels[server].get(name, index)

    def _model_execute(self, server):
        """ Execute model in server. """
        self._exceptions[server] = None
        if server is None:
            try:
                self.workflow.run()
            except Exception as exc:
                self._exceptions[server] = exc
                self._logger.critical('Caught exception: %s' % exc)
        else:
            self._queues[server].put((self._remote_model_execute, server))

    def _remote_model_execute(self, server):
        """ Execute model in remote server. """
        try:
            self._top_levels[server].run()
        except Exception as exc:
            self._exceptions[server] = exc
            self._logger.error('Caught exception from server %s, PID %d on %s: %s',
                               self._server_info[server]['name'],
                               self._server_info[server]['pid'],
                               self._server_info[server]['host'], exc)

    def _model_status(self, server):
        """ Return execute status from model. """
        return self._exceptions[server]


class CaseIteratorDriver(CaseIterDriverBase):
    """
    Run a set of cases provided by an :class:`ICaseIterator`. Concurrent
    evaluation is supported, with the various evaluations executed across
    servers obtained from the :class:`ResourceAllocationManager`.
    """

    iterator = Instance(ICaseIterator, iotype='in',
                        desc='Iterator supplying Cases to evaluate.')
    
    def get_case_iterator(self):
        """Returns a new iterator over the Case set."""
        if self.iterator is not None:
            return self.iterator.__iter__()
        else:
            self.raise_exception("iterator has not been set", ValueError)
