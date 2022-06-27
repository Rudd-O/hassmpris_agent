ROOT_DIR := $(shell dirname "$(realpath $(MAKEFILE_LIST))")

.PHONY = proto

test:
	cd $(ROOT_DIR) && \
	tox --current-env
