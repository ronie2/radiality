"""
radiality:radiality.creation

The `radiality/creation.py` is a part of `radiality`.
Apache 2.0 licensed.
"""

from itertools import zip_longest
from functools import wraps
import json

import asyncio
from websockets.exceptions import ConnectionClosed

from radiality import watch
from radiality import circuit


def event(method):
    """
    Decorator for the definition of an event
    """
    method = asyncio.coroutine(method)
    # Constructs the event specification template
    names = method.__code__.co_varnames[::-1]
    defaults = method.__defaults__[::-1]
    spec_tmpl = list(zip_longest(names, defaults))[::-1]
    spec_tmpl.append(('*event', method.__name__))

    @wraps(method)
    def _wrapper(self, *args, **kwargs):
        spec = {
            name: (value if arg is None else arg)
            for ((name, value), arg) in zip_longest(spec_tmpl, args)
        }
        spec.update(kwargs)

        yield from method(self, *args, **kwargs)
        yield from self._actualize(spec)

    _wrapper._is_event = True

    return asyncio.coroutine(_wrapper)


class Eventer(watch.Loggable, circuit.Connectable):
    """
    Emitter of the specific events
    """
    _effectors = None

    def __init__(self, logger, connector):
        """
        Initialization
        """
        self._logger = logger
        self._connector = connector

        self._effectors = {}

    # overridden from `circuit.Connectable`
    @asyncio.coroutine
    def connect(self, sid, freq):
        if sid in self._effectors:  # => reconnect
            yield from self.disconnect(sid, channel=None)

        channel = yield from super().connect(sid, freq)
        if channel:  # => register a new effector
            self.register_effector(sid, channel)
            yield from self.effector_connected(sid, freq)

        return channel

    # overridden from `circuit.Connectable`
    @asyncio.coroutine
    def disconnect(self, sid, channel):
        channel = self._effectors.pop(sid, channel)

        yield from super().disconnect(sid, channel)
        yield from self.effector_disconnected(sid)

    def register_effector(self, sid, channel):
        self._effectors[sid] = channel

    @asyncio.coroutine
    def effector_connected(self, sid, freq):
        data = {
            '*signal': ('biconnected' if sid in self.wanted else 'connected'),
            'sid': self.sid,
            'freq': self.freq
        }

        try:
            data = json.dumps(data)
            yield from self._effectors[sid].send(data)
        except ValueError:
            self.fail('Invalid output -- could not decode data: %s', str(data))
        except ConnectionClosed:
            self.fail('Connection closed')

    @asyncio.coroutine
    def effector_disconnected(self, sid):
        pass

    @asyncio.coroutine
    def _transmit(self, channel, spec):
        try:
            yield from channel.send(spec)
        except ConnectionClosed:
            self.fail('Connection closed')
        else:
            return True

        return False

    @asyncio.coroutine
    def _actualize(self, spec):
        try:
            spec = json.dumps(spec)
        except ValueError:
            self.fail('Invalid output -- could not decode data: %s', str(spec))
        else:
            for (sid, channel) in list(self._effectors.items()):
                try:
                    yield from channel.send(spec)
                except ConnectionClosed:
                    self.warn('Connection to `%s` closed', sid)
                    # Clears the wasted effector
                    self._effectors.pop(sid)
                    yield from self.effector_disconnected(sid)
