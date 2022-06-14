.PHONY = proto

proto: src/hassmprisintegration/proto/mpris_pb2.py src/hassmprisintegration/proto/mpris_pb2_grpc.py

src/hassmprisintegration/proto/mpris_pb2.py: src/hassmprisintegration/proto/mpris.proto
	python3 -m grpc_tools.protoc \
	  src/hassmprisintegration/proto/mpris.proto \
	  --proto_path=src/hassmprisintegration/proto \
	  --grpc_python_out=src/hassmprisintegration/proto \
	  --python_out=src/hassmprisintegration/proto
	sed -i 's/import mpris_pb2 as mpris__pb2/from . import mpris_pb2 as mpris__pb2/' src/hassmprisintegration/proto/mpris_pb2_grpc.py

src/hassmprisintegration/proto/mpris_pb2_grpc.py: src/hassmprisintegration/proto/mpris.proto
	python3 -m grpc_tools.protoc \
	  src/hassmprisintegration/proto/mpris.proto \
	  --proto_path=src/hassmprisintegration/proto \
	  --grpc_python_out=src/hassmprisintegration/proto \
	  --python_out=src/hassmprisintegration/proto
	sed -i 's/import mpris_pb2 as mpris__pb2/from . import mpris_pb2 as mpris__pb2/' src/hassmprisintegration/proto/mpris_pb2_grpc.py
