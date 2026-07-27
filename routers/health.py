from fastapi import APIRouter

router = APIRouter()


@router.get("/")
def root():
    return {"status": "ok", "service": "backend"}


@router.get("/health")
def health():
    return {"status": "healthy"}
