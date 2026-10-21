"""Bounded visual workflow. No agent runtime or model-executed tools."""

import os

# Image operations do not need a BLAS pool in each concurrent CLI worker.
# Honour explicit deployment settings while keeping batch imports bounded.
for _name in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS"):
    os.environ.setdefault(_name, "1")

__version__ = '6.0.0'
