"""Integration contracts for the frame-based two-cut engine, not legacy single cuts."""
from dataclasses import dataclass, replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np
import shapely
from shapely.geometry import box, LineString

from die_shift_studio.reconstruction import Reconstructor, normalize_via_refs
from die_shift_studio.reconstruction_overlay import CutDrag, nudge_cut
from die_shift_studio.settings import ViewSettings
try:
    from test_reconstruction import chain, example, BRIDGE
except ModuleNotFoundError:
    from tests.test_reconstruction import chain, example, BRIDGE


@dataclass(frozen=True)
class RegionSettings(ViewSettings):
    reconstruction_regions: tuple = ()


class ReconstructionIntegrationTests(unittest.TestCase):
    pass











if __name__=='__main__':unittest.main()
