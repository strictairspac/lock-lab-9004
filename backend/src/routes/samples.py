from fastapi import APIRouter,Depends
from src.config.database import db_session
from src.utils.security import current,writer
from src.schemas.sample import SampleInput
from src.schemas.quantity import BackfillInput
from src.services import samples as service
from src.services import quantity
from fastapi.responses import JSONResponse
router=APIRouter()
@router.get('/samples')
def listing(db=Depends(db_session),user=Depends(current)): return service.listing(db)
@router.post('/samples',status_code=201)
def create(data:SampleInput,db=Depends(db_session),user=Depends(writer)): return service.create(db,data,user)
@router.put('/samples/{ident}')
def update(ident:int,data:SampleInput,db=Depends(db_session),user=Depends(writer)): return service.update(db,ident,data,user)
@router.get('/samples/{ident}/quantity')
def timeline(ident:int,db=Depends(db_session),user=Depends(current)): return service.timeline(db,ident)
@router.post('/samples/{ident}/quantity/backfill',status_code=201)
def backfill(ident:int,data:BackfillInput,db=Depends(db_session),user=Depends(writer)):
    item,replayed=quantity.add_backfill(db,ident,data,user)
    if replayed: return JSONResponse(item,status_code=200,headers={'X-Idempotent-Replay':'true'})
    return item
