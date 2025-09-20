# run-dev.ps1
# Start backend with hot-reload and watch app/, app/api/, app/services

uvicorn app.main:app `
  --reload `
  --reload-dir app `
  --reload-dir app/api `
  --reload-dir app/services
