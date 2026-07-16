# 全新 build:清舊產物後 onefile 打包(PowerShell 執行)
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
Remove-Item POS.spec -ErrorAction SilentlyContinue
pyinstaller --clean --onefile --version-file version_info.txt --icon assets/POS.ico --name POS --add-data "static;static" main.py
