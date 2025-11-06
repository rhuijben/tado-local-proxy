import asyncio

import importlib

from tado_local import zeroconf_register


def test_register_service_uses_zeroconf_when_loop_running_and_avahi_async_scheduled(monkeypatch):
    # Simulate an event loop that is running and create_task available
    class LoopStub:
        def is_running(self):
            return True

        def create_task(self, coro):
            # Simulate a task which immediately invokes callbacks with a future
            class FutureStub:
                def result(self):
                    return ('avahi', 'bus', 'group')

            class TaskStub:
                def add_done_callback(self, cb):
                    # Immediately call callback to simulate completion
                    cb(FutureStub())

            return TaskStub()

    monkeypatch.setattr('asyncio.get_event_loop', lambda: LoopStub())

    # Stub avahi coroutine (we won't actually await it)
    async def fake_avahi(name, service_type, port, props):
        return ('avahi', 'bus', 'group')

    monkeypatch.setattr(zeroconf_register, '_try_avahi_register_async', fake_avahi)

    # Stub zeroconf fallback to ensure it is called and returns success
    def fake_zeroconf(name, service_type, port, props):
        return ('zeroconf', 'zc', 'info')

    monkeypatch.setattr(zeroconf_register, '_try_zeroconf_register', fake_zeroconf)

    ok, method, msg = zeroconf_register.register_service('tado-local-test', 4407, {'path':'/'})
    assert ok is True
    # When loop is running our code registers zeroconf immediately and schedules Avahi async
    assert method == 'zeroconf'


def test_register_service_falls_back_to_zeroconf_when_avahi_unavailable(monkeypatch):
    # Simulate no running loop
    class LoopStub:
        def is_running(self):
            return False

    monkeypatch.setattr('asyncio.get_event_loop', lambda: LoopStub())

    # Make avahi registration raise to simulate missing dbus-next
    async def raise_avahi(*args, **kwargs):
        raise RuntimeError('dbus-next missing')

    monkeypatch.setattr(zeroconf_register, '_try_avahi_register_async', raise_avahi)

    # Zeroconf fallback success
    def fake_zeroconf(name, service_type, port, props):
        return ('zeroconf', 'zc', 'info')

    monkeypatch.setattr(zeroconf_register, '_try_zeroconf_register', fake_zeroconf)

    ok, method, msg = zeroconf_register.register_service('tado-local-test', 4407, {'path':'/'})
    assert ok is True
    assert method == 'zeroconf'
