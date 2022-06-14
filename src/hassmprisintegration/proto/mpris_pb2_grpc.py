# Generated by the gRPC Python protocol compiler plugin. DO NOT EDIT!
"""Client and server classes corresponding to protobuf-defined services."""
import grpc

from . import mpris_pb2 as mpris__pb2


class MPRISServiceStub(object):
    """Missing associated documentation comment in .proto file."""

    def __init__(self, channel):
        """Constructor.

        Args:
            channel: A grpc.Channel.
        """
        self.Updates = channel.unary_stream(
                '/MPRISPackage.MPRISService/Updates',
                request_serializer=mpris__pb2.MPRISUpdateRequest.SerializeToString,
                response_deserializer=mpris__pb2.MPRISUpdateReply.FromString,
                )
        self.ChangePlayerStatus = channel.unary_unary(
                '/MPRISPackage.MPRISService/ChangePlayerStatus',
                request_serializer=mpris__pb2.ChangePlayerStatusRequest.SerializeToString,
                response_deserializer=mpris__pb2.ChangePlayerStatusReply.FromString,
                )
        self.PlayerNext = channel.unary_unary(
                '/MPRISPackage.MPRISService/PlayerNext',
                request_serializer=mpris__pb2.PlayerNextRequest.SerializeToString,
                response_deserializer=mpris__pb2.PlayerNextReply.FromString,
                )
        self.PlayerPrevious = channel.unary_unary(
                '/MPRISPackage.MPRISService/PlayerPrevious',
                request_serializer=mpris__pb2.PlayerPreviousRequest.SerializeToString,
                response_deserializer=mpris__pb2.PlayerPreviousReply.FromString,
                )


class MPRISServiceServicer(object):
    """Missing associated documentation comment in .proto file."""

    def Updates(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def ChangePlayerStatus(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def PlayerNext(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def PlayerPrevious(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')


def add_MPRISServiceServicer_to_server(servicer, server):
    rpc_method_handlers = {
            'Updates': grpc.unary_stream_rpc_method_handler(
                    servicer.Updates,
                    request_deserializer=mpris__pb2.MPRISUpdateRequest.FromString,
                    response_serializer=mpris__pb2.MPRISUpdateReply.SerializeToString,
            ),
            'ChangePlayerStatus': grpc.unary_unary_rpc_method_handler(
                    servicer.ChangePlayerStatus,
                    request_deserializer=mpris__pb2.ChangePlayerStatusRequest.FromString,
                    response_serializer=mpris__pb2.ChangePlayerStatusReply.SerializeToString,
            ),
            'PlayerNext': grpc.unary_unary_rpc_method_handler(
                    servicer.PlayerNext,
                    request_deserializer=mpris__pb2.PlayerNextRequest.FromString,
                    response_serializer=mpris__pb2.PlayerNextReply.SerializeToString,
            ),
            'PlayerPrevious': grpc.unary_unary_rpc_method_handler(
                    servicer.PlayerPrevious,
                    request_deserializer=mpris__pb2.PlayerPreviousRequest.FromString,
                    response_serializer=mpris__pb2.PlayerPreviousReply.SerializeToString,
            ),
    }
    generic_handler = grpc.method_handlers_generic_handler(
            'MPRISPackage.MPRISService', rpc_method_handlers)
    server.add_generic_rpc_handlers((generic_handler,))


 # This class is part of an EXPERIMENTAL API.
class MPRISService(object):
    """Missing associated documentation comment in .proto file."""

    @staticmethod
    def Updates(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_stream(request, target, '/MPRISPackage.MPRISService/Updates',
            mpris__pb2.MPRISUpdateRequest.SerializeToString,
            mpris__pb2.MPRISUpdateReply.FromString,
            options, channel_credentials,
            insecure, call_credentials, compression, wait_for_ready, timeout, metadata)

    @staticmethod
    def ChangePlayerStatus(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(request, target, '/MPRISPackage.MPRISService/ChangePlayerStatus',
            mpris__pb2.ChangePlayerStatusRequest.SerializeToString,
            mpris__pb2.ChangePlayerStatusReply.FromString,
            options, channel_credentials,
            insecure, call_credentials, compression, wait_for_ready, timeout, metadata)

    @staticmethod
    def PlayerNext(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(request, target, '/MPRISPackage.MPRISService/PlayerNext',
            mpris__pb2.PlayerNextRequest.SerializeToString,
            mpris__pb2.PlayerNextReply.FromString,
            options, channel_credentials,
            insecure, call_credentials, compression, wait_for_ready, timeout, metadata)

    @staticmethod
    def PlayerPrevious(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(request, target, '/MPRISPackage.MPRISService/PlayerPrevious',
            mpris__pb2.PlayerPreviousRequest.SerializeToString,
            mpris__pb2.PlayerPreviousReply.FromString,
            options, channel_credentials,
            insecure, call_credentials, compression, wait_for_ready, timeout, metadata)
