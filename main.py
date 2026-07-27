from fastapi import FastAPI

app = FastAPI(title="Techo 360 - Backend")

@app.get("/")
def root():
    return {"status": "ok", "service": "backend"}

@app.get("/health")
def health():
    return {"status": "healthy"}
