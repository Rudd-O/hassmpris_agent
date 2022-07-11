from typing import Any, cast

from dasbus.client.handler import ClientObjectHandler
from dasbus.specification import DBusSpecification


chromium_dbus_interface = """
<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
"http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
  <interface name="org.freedesktop.DBus.Introspectable">
    <method name="Introspect">
      <arg name="data" direction="out" type="s"/>
    </method>
  </interface>
  <interface name="org.freedesktop.DBus.Properties">
    <method name="Get">
      <arg direction="in" type="s"/>
      <arg direction="in" type="s"/>
      <arg direction="out" type="v"/>
    </method>
    <method name="Set">
      <arg direction="in" type="s"/>
      <arg direction="in" type="s"/>
      <arg direction="in" type="v"/>
    </method>
    <method name="GetAll">
      <arg direction="in" type="s"/>
      <arg direction="out" type="a{sv}"/>
    </method>
    <signal name="PropertiesChanged">
      <arg type="s"/>
      <arg type="a{sv}"/>
      <arg type="as"/>
    </signal>
  </interface>
  <interface name="org.mpris.MediaPlayer2">
    <property name="Identity" type="s" access="read" />
    <property name="SupportedMimeTypes" type="as" access="read" />
    <property name="SupportedUriSchemes" type="as" access="read" />
    <property name="HasTrackList" type="b" access="read" />
    <property name="CanQuit" type="b" access="read" />
    <property name="CanRaise" type="b" access="read" />
    <method name="Quit" />
    <method name="Raise" />
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <property name="Metadata" type="a{sv}" access="read" />
    <property name="PlaybackStatus" type="s" access="read" />
    <property name="LoopStatus" type="s" access="readwrite" />
    <property name="Volume" type="d" access="readwrite" />
    <property name="Shuffle" type="d" access="readwrite" />
    <property name="Position" type="i" access="read" />
    <property name="Rate" type="d" access="readwrite" />
    <property name="MinimumRate" type="d" access="readwrite" />
    <property name="MaximumRate" type="d" access="readwrite" />
    <property name="CanControl" type="b" access="read" />
    <property name="CanPlay" type="b" access="read" />
    <property name="CanPause" type="b" access="read" />
    <property name="CanSeek" type="b" access="read" />
    <property name="CanGoNext" type="b" access="read" />
    <property name="CanGoPrevious" type="b" access="read" />
    <method name="Next" />
    <method name="Previous" />
    <method name="Pause" />
    <method name="PlayPause" />
    <method name="Stop" />
    <method name="Play" />
    <method name="Seek">
      <arg type="x" direction="in" />
    </method>    <method name="OpenUri">
      <arg type="s" direction="in" />
    </method>
    <method name="SetPosition">
      <arg type="o" direction="in" />
      <arg type="x" direction="in" />
    </method>
    <signal name="Seeked">
      <arg type="x"/>
    </signal>
  </interface>
</node>
"""


class ChromiumObjectHandler(ClientObjectHandler):
    """
    Exists to cover up the lack of D-Bus introspection in Chromium.
    """

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._specification = DBusSpecification.from_xml(
            chromium_dbus_interface,
        )

    def create_member(self, *a: Any, **kw: Any) -> Any:
        return ClientObjectHandler.create_member(self, *a, **kw)


if __name__ == "__main__":
    import sys
    from dasbus.connection import SessionMessageBus
    from dasbus.connection import InterfaceProxy

    name = sys.argv[1]
    bus = SessionMessageBus()
    properties_proxy = cast(
        InterfaceProxy,
        bus.get_proxy(
            name,
            "/org/mpris/MediaPlayer2",
            interface_name="org.freedesktop.DBus.Properties",
        ),
    )
    x = properties_proxy.GetAll("org.mpris.MediaPlayer2.Player")
    print(x)
