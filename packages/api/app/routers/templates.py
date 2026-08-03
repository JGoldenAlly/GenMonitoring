import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models import ModbusProfileTemplate, User
from app.schemas.templates import TemplateCreate, TemplateOut, TemplateUpdate

router = APIRouter(prefix="/templates", tags=["templates"])


async def _get_template_or_404(template_id: UUID, db: AsyncSession) -> ModbusProfileTemplate:
    template = await db.get(ModbusProfileTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return template


@router.get("", response_model=list[TemplateOut])
async def list_templates(
    _user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[ModbusProfileTemplate]:
    result = await db.execute(select(ModbusProfileTemplate).order_by(ModbusProfileTemplate.name))
    return list(result.scalars().all())


@router.post(
    "",
    response_model=TemplateOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin", "operator"))],
)
async def create_template(body: TemplateCreate, db: AsyncSession = Depends(get_db)) -> ModbusProfileTemplate:
    template = ModbusProfileTemplate(
        slug=f"custom-{uuid.uuid4().hex[:8]}",
        name=body.name,
        description=body.description,
        category=body.category,
        registers=[r.model_dump() for r in body.registers],
        is_builtin=False,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return template


@router.put("/{template_id}", response_model=TemplateOut)
async def update_template(
    template_id: UUID,
    body: TemplateUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ModbusProfileTemplate:
    template = await _get_template_or_404(template_id, db)
    if template.is_builtin and user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only admins may edit builtin templates"
        )
    if user.role not in ("admin", "operator"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

    data = body.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(template, field, value)
    await db.commit()
    await db.refresh(template)
    return template


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    template = await _get_template_or_404(template_id, db)
    if template.is_builtin and user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only admins may delete builtin templates"
        )
    if user.role not in ("admin", "operator"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
    await db.delete(template)
    await db.commit()
