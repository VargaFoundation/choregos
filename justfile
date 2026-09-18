# Équivalent just du Makefile (mêmes cibles)
set shell := ["bash", "-uc"]

default: help
help: ; @make help
ci: ; @make ci
lint: ; @make lint
format: ; @make format
typecheck: ; @make typecheck
test: ; @make test
contracts: ; @make contracts
dev-up: ; @make dev-up
dev-down: ; @make dev-down
dev-seed: ; @make dev-seed
demo: ; @make demo
e2e: ; @make e2e
