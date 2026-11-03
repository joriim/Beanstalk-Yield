# SPDX-License-Identifier: GPL-2.0-or-later
"""Processing algorithms. Each one is a thin wrapper around a yieldsuite core function."""

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
)


class GridYieldAlgorithm(QgsProcessingAlgorithm):
    """L3 -> L4: apportion record mass to a fixed grid (yieldsuite.l4.apportion_mass)."""

    INPUT = "INPUT"
    MASS = "MASS"
    FLAGS = "FLAGS"
    RESOLUTION = "RESOLUTION"
    OUTPUT = "OUTPUT"

    def name(self):
        return "grid_yield"

    def displayName(self):  # noqa: N802
        return "L4 · Grid yield (mass apportioning)"

    def group(self):
        return "Processing levels"

    def groupId(self):  # noqa: N802
        return "levels"

    def shortHelpString(self):  # noqa: N802
        return (
            "Apportions the mass of each record polygon to grid cells snapped to a fixed "
            "origin, so the field total is conserved. Flagged records reduce coverage "
            "instead of biasing yield. Writes a multi-band GeoTIFF: yield, coverage, "
            "n_records, spread, flagged_share, passes."
        )

    def createInstance(self):  # noqa: N802
        return GridYieldAlgorithm()

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterFeatureSource(self.INPUT, "L3 record polygons"))
        self.addParameter(
            QgsProcessingParameterField(self.MASS, "Mass field [kg]", parentLayerParameterName=self.INPUT)
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.FLAGS, "Flag bitmask field", parentLayerParameterName=self.INPUT, optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.RESOLUTION, "Cell size [m]", QgsProcessingParameterNumber.Double, 20.0, minValue=1.0
            )
        )
        self.addParameter(QgsProcessingParameterRasterDestination(self.OUTPUT, "Yield grid"))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        try:
            import yieldsuite  # noqa: F401
        except ImportError as exc:
            raise QgsProcessingException(
                "The yieldsuite library is not installed in QGIS's Python. "
                "Install it with: pip install yieldsuite"
            ) from exc
        # TODO: read polygons via QgsFeatureRequest -> shapely, call
        # yieldsuite.l4.apportion_mass, write with yieldsuite.l4.io.write_cog.
        raise QgsProcessingException("Grid yield is not wired up yet")
