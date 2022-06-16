ROOT_DIR := $(shell dirname "$(realpath $(MAKEFILE_LIST))")

.PHONY = proto clean

proto: \
        src/hassmpris/proto/mpris_pb2.py \
        src/hassmpris/proto/mpris_pb2_grpc.py \
        src/hassmpris/security/proto/ecdh_pb2.py \
        src/hassmpris/security/proto/ecdh_pb2_grpc.py \
        src/hassmpris/security/proto/masc_pb2.py \
        src/hassmpris/security/proto/masc_pb2_grpc.py

test:
	cd $(ROOT_DIR) && \
	PYTHONPATH=$(PWD)/src echo pytest -v . && \
	PYTHONPATH=$(PWD)/src mypy .

clean:
	rm -f src/hassmpris/proto/mpris_pb2.py
	rm -f src/hassmpris/proto/mpris_pb2_grpc.py
	rm -f src/hassmpris/security/proto/ecdh_pb2.py
	rm -f src/hassmpris/security/proto/ecdh_pb2_grpc.py
	rm -f src/hassmpris/security/proto/masc_pb2.py
	rm -f src/hassmpris/security/proto/masc_pb2_grpc.py
	
src/hassmpris/proto/mpris_pb2.py: src/hassmpris/proto/mpris.proto
	python3 -m grpc_tools.protoc \
	  src/hassmpris/proto/mpris.proto \
	  --proto_path=src/hassmpris/proto \
	  --grpc_python_out=src/hassmpris/proto \
	  --python_out=src/hassmpris/proto
	sed -i 's/import mpris_pb2 as mpris__pb2/from hassmpris.proto import mpris_pb2 as mpris__pb2/' src/hassmpris/proto/mpris_pb2_grpc.py

src/hassmpris/proto/mpris_pb2_grpc.py: src/hassmpris/proto/mpris.proto
	python3 -m grpc_tools.protoc \
	  src/hassmpris/proto/mpris.proto \
	  --proto_path=src/hassmpris/proto \
	  --grpc_python_out=src/hassmpris/proto \
	  --python_out=src/hassmpris/proto
	sed -i 's/import mpris_pb2 as mpris__pb2/from hassmpris.proto import mpris_pb2 as mpris__pb2/' src/hassmpris/proto/mpris_pb2_grpc.py

src/hassmpris/security/proto/ecdh_pb2.py: src/hassmpris/security/proto/ecdh.proto
	python3 -m grpc_tools.protoc \
	  src/hassmpris/security/proto/ecdh.proto \
	  --proto_path=src/hassmpris/security/proto \
	  --grpc_python_out=src/hassmpris/security/proto \
	  --python_out=src/hassmpris/security/proto
	sed -i 's/import ecdh_pb2 as ecdh__pb2/from hassmpris.security.proto import ecdh_pb2 as ecdh__pb2/' src/hassmpris/security/proto/ecdh_pb2_grpc.py

src/hassmpris/security/proto/ecdh_pb2_grpc.py: src/hassmpris/security/proto/ecdh.proto
	python3 -m grpc_tools.protoc \
	  src/hassmpris/security/proto/ecdh.proto \
	  --proto_path=src/hassmpris/security/proto \
	  --grpc_python_out=src/hassmpris/security/proto \
	  --python_out=src/hassmpris/security/proto
	sed -i 's/import ecdh_pb2 as ecdh__pb2/from hassmpris.security.proto import ecdh_pb2 as ecdh__pb2/' src/hassmpris/security/proto/ecdh_pb2_grpc.py

src/hassmpris/security/proto/masc_pb2.py: src/hassmpris/security/proto/masc.proto
	python3 -m grpc_tools.protoc \
	  src/hassmpris/security/proto/masc.proto \
	  --proto_path=src/hassmpris/security/proto \
	  --grpc_python_out=src/hassmpris/security/proto \
	  --python_out=src/hassmpris/security/proto
	sed -i 's/import masc_pb2 as masc__pb2/from hassmpris.security.proto import masc_pb2 as masc__pb2/' src/hassmpris/security/proto/masc_pb2_grpc.py

src/hassmpris/security/proto/masc_pb2_grpc.py: src/hassmpris/security/proto/masc.proto
	python3 -m grpc_tools.protoc \
	  src/hassmpris/security/proto/masc.proto \
	  --proto_path=src/hassmpris/security/proto \
	  --grpc_python_out=src/hassmpris/security/proto \
	  --python_out=src/hassmpris/security/proto
	sed -i 's/import masc_pb2 as masc__pb2/from hassmpris.security.proto import masc_pb2 as masc__pb2/' src/hassmpris/security/proto/masc_pb2_grpc.py
