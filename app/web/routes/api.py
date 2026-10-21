"""მცირე JSON მოთხოვნები ფორმებისთვის: ძიება/სკანირება და ინგრედიენტის სწრაფი შექმნა."""
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.formatting import fmt_qty
from app.models.documents import ItemType
from app.models.system import User
from app.services import ingredients as ing_svc
from app.services import products as prod_svc
from app.services import stock
from app.services.errors import ValidationError
from app.services.text import barcode_key
from app.web.deps import get_current_user, get_db
from app.web.forms import get_int

router = APIRouter(prefix="/api")


@router.get("/ingredients/lookup")
def ingredient_lookup(q: str = "", db: Session = Depends(get_db)):
    result = ing_svc.search_ingredients(db, q, "active", limit=15)
    bal = stock.balances(db, ItemType.INGREDIENT, ids=[i.id for i in result.items])
    items = [{
        "id": i.id,
        "name": i.name,
        "sub": " · ".join(b.barcode for b in i.barcodes),
        "stock": fmt_qty(bal.get(i.id, Decimal(0)), i.unit),
    } for i in result.items]
    exact = result.exact_barcode[0].id if len(result.exact_barcode) == 1 else None
    return {"q": q, "exact_id": exact, "items": items}


@router.get("/products/lookup")
def product_lookup(q: str = "", db: Session = Depends(get_db)):
    found = prod_svc.search_products(db, q, "active", limit=15)
    bal = stock.balances(db, ItemType.PRODUCT, ids=[p.id for p in found])
    key = barcode_key(q)
    exact = [p for p in found if key and (p.barcode_norm == key or (p.code or "").casefold() == q.strip().casefold())]
    items = [{
        "id": p.id,
        "name": p.name,
        "sub": " · ".join(x for x in [p.code or "", p.barcode or ""] if x),
        "stock": fmt_qty(bal.get(p.id, Decimal(0)), p.unit),
    } for p in found]
    return {"q": q, "exact_id": exact[0].id if len(exact) == 1 else None, "items": items}


@router.post("/ingredients/quick")
async def ingredient_quick_create(request: Request, db: Session = Depends(get_db),
                                  user: User | None = Depends(get_current_user)):
    form = await request.form()
    data = ing_svc.IngredientInput(
        name=str(form.get("name", "")),
        unit_id=get_int(form.get("unit_id")),
        min_stock=Decimal(0),
        barcodes=[str(form.get("barcode", ""))],
    )
    try:
        ing = ing_svc.create_ingredient(db, data, user)
        db.commit()
    except ValidationError as e:
        db.rollback()
        return JSONResponse({"ok": False, "errors": e.rendered()})
    return {"ok": True, "id": ing.id, "name": ing.name}
