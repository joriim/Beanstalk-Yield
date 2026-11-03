# SPDX-License-Identifier: GPL-2.0-or-later
"""QGIS plugin entry point."""


def classFactory(iface):  # noqa: N802 - name required by QGIS
    from .plugin import YieldSuitePlugin

    return YieldSuitePlugin(iface)
