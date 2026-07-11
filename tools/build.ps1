# 全新 build:清舊產物後 onefile 打包(PowerShell 執行)
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
Remove-Item POS.spec -ErrorAction SilentlyContinue
pyinstaller --onefile --name POS --add-data "static;static" `
  --hidden-import uvicorn.logging --hidden-import uvicorn.loops.auto `
  --hidden-import uvicorn.protocols.http.auto --hidden-import uvicorn.lifespan.on `
  main.py
