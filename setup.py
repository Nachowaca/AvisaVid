"""
Empaqueta AvisaVid como app de macOS (.app) con py2app.

Uso (solo en macOS):
    pip3 install py2app
    python3 setup.py py2app

Genera dist/AvisaVid.app. Después armamos el .dmg con hdiutil (ver README).
"""
from setuptools import setup

APP = ["app.py"]
DATA_FILES = [
    ("fonts", ["fonts/PEPSI_pl.ttf"]),
]
OPTIONS = {
    "argv_emulation": False,
    "iconfile": "icon.icns",
    "plist": {
        "CFBundleName": "AvisaVid",
        "CFBundleDisplayName": "AvisaVid",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleIdentifier": "com.nacho.avisavid",
        "NSHighResolutionCapable": True,
    },
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
