import os
import signal
import threading

import dasbus.error
from dasbus.loop import EventLoop
from dasbus.connection import SessionMessageBus


class AlreadyRunning(Exception):
    pass


CMD_RESTART = signal.SIGUSR1
CMD_RESET_PAIRINGS = signal.SIGUSR2
CMD_QUIT = signal.SIGTERM


class HASSMPRISControl(object):

    __dbus_xml__ = """
    <node>
        <interface name="com.rudd_o.HASSMPRIS1">
            <method name="Ping">
            </method>
            <method name="Quit">
            </method>
            <method name="Restart">
            </method>
            <method name="ResetPairings">
            </method>
        </interface>
    </node>
    """

    def __init__(self) -> None:
        self.loop = EventLoop()
        self.thread = threading.Thread(target=self.loop.run)

    def start(self) -> None:
        bus = SessionMessageBus()
        bus.publish_object("/", self)
        try:
            bus.register_service("com.rudd_o.HASSMPRIS1")
        except dasbus.error.DBusError as e:
            if "Name request has failed" in str(e):
                raise AlreadyRunning()
        self.thread.start()

    def stop(self) -> None:
        self.loop.quit()
        self.thread.join()

    def Ping(self) -> None:
        pass

    def Restart(self) -> None:
        os.kill(os.getpid(), CMD_RESTART)

    def ResetPairings(self) -> None:
        os.kill(os.getpid(), CMD_RESET_PAIRINGS)

    def Quit(self) -> None:
        os.kill(os.getpid(), CMD_QUIT)
