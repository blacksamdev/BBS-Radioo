#!/bin/bash

set -e

export PYTHONUNBUFFERED=1
export PYTHONPATH=/app/lib/bbs-radioo

exec python3 -m bbs_radioo.main "$@"
