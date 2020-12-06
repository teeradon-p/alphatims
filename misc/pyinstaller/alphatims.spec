# -*- mode: python ; coding: utf-8 -*-

import pkgutil
import os
import sys
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT, BUNDLE, TOC
import PyInstaller.utils.hooks
import pkg_resources
import importlib.metadata
import alphatims


##################### User definitions
exe_name = 'alphatims_gui'
script_name = 'alphatims_pyinstaller.py'
if sys.platform[:6] == "darwin":
	icon = '../alpha_logo.icns'
else:
	icon = '../alpha_logo.ico'
block_cipher = None
location = os.getcwd()
project = "alphatims"
remove_tests = True
bundle_name = "alphatims"
bundle_identifier = f"{bundle_name}.{alphatims.__version__}"
#####################


requirements = {
	req.split()[0] for req in importlib.metadata.requires(project)
}
requirements.add(project)
requirements.add("distributed")
hidden_imports = set()
datas = []
binaries = []
checked = set()
while requirements:
	requirement = requirements.pop()
	checked.add(requirement)
	if requirement in ["pywin32"]:
		continue
	try:
		module_version = importlib.metadata.version(requirement)
	except (
		importlib.metadata.PackageNotFoundError,
		ModuleNotFoundError,
		ImportError
	):
		continue
	try:
		datas_, binaries_, hidden_imports_ = PyInstaller.utils.hooks.collect_all(
			requirement,
			include_py_files=True
		)
	except ImportError:
		continue
	datas += datas_
	# binaries += binaries_
	hidden_imports_ = set(hidden_imports_)
	if "" in hidden_imports_:
		hidden_imports_.remove("")
	if None in hidden_imports_:
		hidden_imports_.remove(None)
	requirements |= hidden_imports_ - checked
	hidden_imports |= hidden_imports_

if remove_tests:
	hidden_imports = sorted(
		[h for h in hidden_imports if "tests" not in h.split(".")]
	)
else:
	hidden_imports = sorted(hidden_imports)


hidden_imports = [h for h in hidden_imports if "__pycache__" not in h]
datas = [d for d in datas if "__pycache__" not in d[0]]


a = Analysis(
	[script_name],
	pathex=[location],
	binaries=binaries,
	datas=datas,
	hiddenimports=hidden_imports,
	hookspath=[],
	runtime_hooks=[],
	excludes=[h for h in hidden_imports if "datashader" in h],
	win_no_prefer_redirects=False,
	win_private_assemblies=False,
	cipher=block_cipher,
	noarchive=False
)
pyz = PYZ(
	a.pure,
	a.zipped_data,
	cipher=block_cipher
)
exe = EXE(
	pyz,
	a.scripts,
	# a.binaries,
	a.zipfiles,
	# a.datas,
	exclude_binaries=True,
	name=exe_name,
	debug=False,
	bootloader_ignore_signals=False,
	strip=False,
	upx=True,
	console=True,
	icon=icon
)
coll = COLLECT(
	exe,
	a.binaries,
	# a.zipfiles,
	a.datas,
	strip=False,
	upx=True,
	upx_exclude=[],
	name=exe_name
)

if sys.platform[:6] == "darwin":
	app = BUNDLE(
		coll,
		name=bundle_name,
		icon=icon,
		bundle_identifier=bundle_identifier,
		console=True
	)
