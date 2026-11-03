# SPDX-License-Identifier: GPL-2.0-or-later
from qgis.core import QgsApplication

from .provider import YieldSuiteProvider


class YieldSuitePlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None

    def initProcessing(self):  # noqa: N802
        self.provider = YieldSuiteProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):  # noqa: N802
        self.initProcessing()

    def unload(self):
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
