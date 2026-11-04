# SPDX-License-Identifier: GPL-2.0-or-later
from qgis.core import QgsProcessingProvider

from .algorithms import ALGORITHMS


class YieldSuiteProvider(QgsProcessingProvider):
    def id(self):
        return "yieldsuite"

    def name(self):
        return "Yield Data Suite"

    def longName(self):  # noqa: N802
        return "Yield Data Suite: yield maps from harvester, drone and satellite data"

    def loadAlgorithms(self):  # noqa: N802
        for alg in ALGORITHMS:
            self.addAlgorithm(alg())
