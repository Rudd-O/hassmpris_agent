from .servicer import MPRISServiceServicer
from .mpris import MPRIS
from .rpc import make_server


def server():
    busthread = MPRIS()
    servicer = MPRISServiceServicer(busthread)
    rpc = make_server(servicer)

    busthread.start()
    print("Started D-Bus thread")
    rpc.start()
    print("Started gRPC server: 0.0.0.0:50051")

    try:
        rpc.wait_for_termination()
    except KeyboardInterrupt:
        print("KeyboardInterrupt")

    print("Ending servicer")
    servicer.end()
    print("Ending bus thread")
    busthread.end()
    print("Ended")


def main():
    server()


if __name__ == "__main__":
    server()
