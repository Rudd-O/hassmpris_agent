from typing import TypeVar, Tuple, cast, Any, Optional
import threading
import time


K = TypeVar("K")
V = TypeVar("V")


class TimedDict(dict[K, V]):
    def __init__(self, timeout: int, size_limit: int) -> None:
        self.timeout = timeout
        self.size_limit = size_limit
        self.lock = threading.RLock()

    def __enter__(self) -> None:
        self.lock.acquire()

    def __exit__(  # type: ignore  # noqa
        self,
        unused_type,
        unused_value,
        unused_traceback,
    ) -> None:
        self.lock.release()

    def __trim_timed_out_and_oversized(
        self, minusone: bool, key: Optional[V] = None
    ) -> None:
        now = time.time()
        for k, v in list(self.items()):
            then, _ = cast(Tuple[float, V], v)
            if now - then > self.timeout:
                del self[k]
        if minusone and dict.__contains__(self, key):  # noqa
            minusone = False
        size_limit = self.size_limit - (1 if minusone else 0)
        while len(self) > size_limit:
            kkey = list(self.keys())[0]
            del self[kkey]

    def __setitem__(self, key: K, value: V) -> None:
        with self.lock:
            self.__trim_timed_out_and_oversized(True)
            now = time.time()
            real: Tuple[float, V] = (now, value)
            dict.__setitem__(self, cast(Any, key), real)  # type: ignore

    def __contains__(self, item: K) -> bool:  # type: ignore
        with self.lock:
            self.__trim_timed_out_and_oversized(True)
        return dict.__contains__(self, cast(Any, item))

    def __getitem__(self, key: K) -> V:
        with self.lock:
            self.__trim_timed_out_and_oversized(False)
            _, value = cast(Tuple[float, V], dict.__getitem__(self, key))
            return value


if __name__ == "__main__":
    d: TimedDict[str, int] = TimedDict(1, 100)
    d["a"] = 1
    d["b"] = 1
    d["c"] = 1
    d["d"] = 1
    print(d)
    time.sleep(2)
    print("d" in d)
