from app.db.base import Base

from datetime import datetime

from sqlalchemy import ForeignKey, func, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column

class Kundali(Base):
    __tablename__ = "kundali"

    id: Mapped[int] = mapped_column(primary_key= True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"), nullable=False, unique=True)
    tech_stack: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    focus_areas: Mapped[dict | None] = mapped_column(JSON, nullable = True)
    cadence_baseline : Mapped[dict | None] = mapped_column(JSON, nullable= True)
    recent_shifts : Mapped[dict | None] = mapped_column(JSON, nullable= True)
    created_at : Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default= func.now())




'''
### `kundali_profiles`
| Field | Notes |
|---|---|
| `id` | PK |
| `target_id` | FK → `targets`, one-to-one |
| `tech_stack` | JSON |
| `focus_areas` | JSON |
| `cadence_baseline` | JSON — e.g. weekly commit counts array |
| `recent_shifts` | JSON |
| `created_at` | |
'''
