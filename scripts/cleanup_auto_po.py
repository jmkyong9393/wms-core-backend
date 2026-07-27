from sqlmodel import Session, text
from app.core.database import engine
with Session(engine) as session:
    session.execute(text("DELETE FROM order_items WHERE order_id IN (SELECT id FROM orders WHERE type='AUTO_PO')"))
    session.execute(text("DELETE FROM orders WHERE type='AUTO_PO'"))
    session.commit()
    print("Old POs successfully deleted!")
