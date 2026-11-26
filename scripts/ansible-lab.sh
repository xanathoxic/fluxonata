#!/usr/bin/env bash
# Laboratoire Ansible : machines SSH jetables + runner dédié (voir scripts/ansible_lab.py). Usage : scripts/ansible-lab.sh up|down|status|clean|agent-up|agent-down
exec "$(dirname "$0")/../.venv/bin/python" "$(dirname "$0")/ansible_lab.py" "$@"
