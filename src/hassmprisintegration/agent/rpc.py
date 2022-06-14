import grpc
from concurrent import futures
from ..proto import mpris_pb2_grpc


def make_server(servicer):
    # initialize server with 4 workers
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))

    # attach servicer method to the server
    mpris_pb2_grpc.add_MPRISServiceServicer_to_server(servicer, server)

    # start the server on the port 50051
    server.add_insecure_port("0.0.0.0:50051")
    return server
