# SPDX-License-Identifier: GPL-2.0-or-later
from qgis.core import QgsProcessingProvider

from .algorithms import GridYieldAlgorithm


class YieldSuiteProvider(QgsProcessingProvider):
    def id(self):
        return "yieldsuite"

    def name(self):
        return "Yield Data Suite"

    def loadAlgorithms(self):  # noqa: N802
        self.addAlgorithm(GridYieldAlgorithm())
