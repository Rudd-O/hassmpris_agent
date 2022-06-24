import collections
import logging
import shortauthstrings
import threading

from typing import Dict, Optional, Deque, Any, Tuple, Set

import gi

gi.require_version("Notify", "0.7")
gi.require_version("Gtk", "4.0")
from gi.repository import Notify, Gtk, GLib  # noqa


_LOGGER = logging.getLogger(__name__)


class PeerVerificationMessageDialog(Gtk.MessageDialog):
    ACCEPT = 0
    REJECT = 1
    BLOCK = 2

    def __init__(self, peer: str, key: bytes):
        Gtk.MessageDialog.__init__(self)
        self.peer = peer
        self.key = key

        d = self

        d.set_title("Multimedia remote control settings")
        d.add_button("Match", self.ACCEPT)
        d.add_button("Do not match", self.REJECT)
        d.add_button("This is unsolicited", self.BLOCK)

        box = d.get_message_area()
        while box.get_last_child():
            box.remove(box.get_last_child())

        lb = Gtk.Label()
        lb.set_markup(
            f"Home Assistant <i>{peer}</i> is trying to pair with this "
            "computer.  After pairing, you will be able to control media "
            "playback via Home Assistant.  For your security, we will now "
            "verify the identity of Home Assistant.",
        )
        lb.set_max_width_chars(60)
        lb.set_wrap(True)
        box.append(lb)

        emojis = shortauthstrings.emoji(key, 6)
        lb = Gtk.Label()
        lb.set_markup(f"<big><big><big>{emojis}</big></big></big>")
        box.append(lb)

        lb = Gtk.Label()
        q = "Do the emojis above match what Home Assistant displays onscreen?"
        lb.set_markup(q)
        box.append(lb)


class PeerVerificationResult(object):
    def __init__(self) -> None:
        self.event = threading.Event()
        self.event.clear()
        self.value: bool = False

    def set(self, value: bool) -> None:
        self.value = value
        self.event.set()

    def get(self, timeout: Optional[float] = None) -> bool:
        self.event.wait(timeout)
        return self.value


def peerkey(peer: str, key: bytes) -> bytes:
    return peer.encode("utf-8") + key


class PeerVerificationUI(threading.Thread):
    name = "Multimedia remote control settings"
    inited = False

    def __init__(
        self,
        verify_timeout: float = 60.0,
        max_pending_verifications: int = 4,
    ) -> None:
        threading.Thread.__init__(self, name="Verification UI", daemon=True)
        if not self.inited:
            Notify.init(self.__class__.name)
            self.__class__.inited = True
        self.verifications: Dict[bytes, PeerVerificationResult] = {}
        self.verification_queue: Deque[Tuple[str, Any]] = collections.deque()
        self.blocked: Set[str] = set()
        self.threadlock = threading.RLock()
        self.open_notifications: Set[Notify.Notification] = set()
        self.verify_timeout = verify_timeout
        self.max_pending_verifications = max_pending_verifications

        self.loop = GLib.MainLoop()

    def stop(self) -> None:
        self.loop.quit()
        self.join()

    def verify(self, peer: str, key: bytes) -> bool:
        # First, we strip the port from the peer.
        p = peer.rsplit(":")
        try:
            int(p[-1], 10)
            peer = ":".join(p[:-1])
        except Exception:
            pass

        # Then, we continue with the rest.
        with self.threadlock:
            if self.is_blocked(peer):
                return False
            if len(self.verification_queue) > self.max_pending_verifications:
                return False
            if peerkey(peer, key) in self.verifications:
                q = self.verifications[peerkey(peer, key)]
            else:
                q = PeerVerificationResult()
                self.verifications[peerkey(peer, key)] = q
                f = lambda: self.__start_verification(peer, key)  # noqa: E731
                self.verification_queue.append((peer, f))
                self.__process_next_in_line()
        return q.get(timeout=self.verify_timeout)

    def accept(self, peer: str, key: bytes) -> None:
        with self.threadlock:
            self.verifications[peerkey(peer, key)].set(True)

    def reject(self, peer: str, key: bytes) -> None:
        with self.threadlock:
            self.verifications[peerkey(peer, key)].set(False)

    def block(self, peer: str) -> None:
        with self.threadlock:
            self.blocked.add(peer)

    def is_blocked(self, peer: str) -> bool:
        with self.threadlock:
            return peer in self.blocked

    def __start_verification(self, peer: str, key: bytes) -> None:
        t = "Home Assistant at %s wants to control multimedia playback" % peer
        n = Notify.Notification.new(self.name, t, "dialog-information")
        n.continue_with_next_on_close = True
        n.set_timeout(int(self.verify_timeout * 1000))
        self.open_notifications.add(n)

        def continue_(*unused_args: Any) -> None:
            n.continue_with_next_on_close = False
            d = PeerVerificationMessageDialog(peer, key)
            d.connect("response", self.__verification_response)
            d.show()

        def reject(*unused_args: Any) -> None:
            self.reject(peer, key)

        def block(*unused_args: Any) -> None:
            self.reject(peer, key)
            self.block(peer)

        def closed(*unused_args: Any) -> None:
            try:
                n.disconnect_by_func(closed)
            except ImportError:
                pass
            self.open_notifications.remove(n)
            if n.continue_with_next_on_close:
                self.__process_next_in_line()

        n.add_action("verify", "Verify", continue_)
        n.add_action("reject", "Reject", reject)
        n.add_action("block", "Block", block)
        n.connect("closed", closed)
        n.show()

    def __process_next_in_line(self) -> None:
        with self.threadlock:
            while len(self.verification_queue):
                peer, f = self.verification_queue.popleft()
                if self.is_blocked(peer):
                    continue
                GLib.idle_add(f)
                return

    def __verification_response(
        self, dialog: PeerVerificationMessageDialog, response: int
    ) -> None:
        dialog.disconnect_by_func(self.__verification_response)
        dialog.close()
        if response == dialog.ACCEPT:
            # Peer/key match.
            # Client does not need to retry.
            self.accept(dialog.peer, dialog.key)
        elif response == dialog.REJECT:
            # Does not match.
            # Client is free to retry
            self.reject(dialog.peer, dialog.key)
        elif response == dialog.BLOCK:
            # Unsolicited.
            # Client is not free to retry.
            self.reject(dialog.peer, dialog.key)
            self.block(dialog.peer)
        elif response < 0:
            # Dialog closed with Escape.
            # Client is free to retry.
            self.reject(dialog.peer, dialog.key)
        else:
            assert 0, "not reached"
        self.__process_next_in_line()


def __verifierthread(loop: GLib.MainLoop) -> None:
    ui = PeerVerificationUI(verify_timeout=5)
    print("Attempting first verification")
    verification = ui.verify("peer", b"key")
    print("Verification result:", verification)
    print("Attempting second verification same peer different key")
    verification = ui.verify("peer", b"key2")
    print("Verification result:", verification)
    print("Attempting second verification same peer same old key")
    print("Verification result:", verification)
    verification = ui.verify("peer", b"key")
    print("Attempting second verification new peer old key")
    print("Verification result:", verification)
    verification = ui.verify("peer2", b"key")
    GLib.idle_add(loop.quit)


if __name__ == "__main__":
    loop = GLib.MainLoop()
    threading.Thread(target=__verifierthread, args=(loop,)).start()
    loop.run()
