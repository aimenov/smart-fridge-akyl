from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from sqlalchemy.orm import Session

from backend.app.models.entities import ItemStatus
from backend.app.modules.inventory_service import list_items_with_product
from backend.app.schemas.dto import PantryLine, RecipeOut, RecipeSuggestResponse


@dataclass
class RecipeRow:
    id: str
    title: str
    ingredients: list[str]
    prep_minutes: int


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_recipe_corpus() -> list[RecipeRow]:
    path = _project_root() / "data" / "recipes.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        RecipeRow(
            id=r["id"],
            title=r["title"],
            ingredients=[x.lower().strip() for x in r["ingredients"]],
            prep_minutes=int(r["prep_minutes"]),
        )
        for r in raw
    ]


def _pantry_covers(pantry_names: Iterable[str], ingredient: str) -> bool:
    ing = ingredient.lower()
    for name in pantry_names:
        n = name.lower()
        if ing in n or n in ing:
            return True
    return False


def build_pantry_lines(db: Session, *, include_expired: bool = False) -> tuple[list[str], list[PantryLine]]:
    items = list_items_with_product(db, expiring_only=False)
    names: list[str] = []
    lines: list[PantryLine] = []
    for item in items:
        if not include_expired and item.status == ItemStatus.expired:
            continue
        label = item.product.canonical_name
        names.append(label)
        expiring_soon = item.status == ItemStatus.expiring
        lines.append(
            PantryLine(name=label, quantity_hint=str(item.quantity), expiring_soon=expiring_soon)
        )
    return names, lines


def suggest_recipes(db: Session, *, include_expired: bool = False) -> RecipeSuggestResponse:
    pantry_names, pantry_lines = build_pantry_lines(db, include_expired=include_expired)
    expiring_names = {p.name for p in pantry_lines if p.expiring_soon}

    corpus = load_recipe_corpus()
    scored: list[tuple[RecipeRow, list[str], list[str], float, int, bool]] = []

    for recipe in corpus:
        missing: list[str] = []
        uses_expiring: list[str] = []
        for ing in recipe.ingredients:
            if not _pantry_covers(pantry_names, ing):
                missing.append(ing)
            else:
                for pname in pantry_names:
                    if _pantry_covers([pname], ing) and pname in expiring_names:
                        if pname not in uses_expiring:
                            uses_expiring.append(pname)
        covered = len(recipe.ingredients) - len(missing)
        coverage = covered / max(len(recipe.ingredients), 1)
        missing_count = len(missing)
        prefers_expiring = len(uses_expiring) > 0
        scored.append(
            (recipe, missing, uses_expiring, coverage, missing_count, prefers_expiring)
        )

    def sort_key(row: tuple) -> tuple:
        recipe, missing, uses_expiring, coverage, missing_count, prefers_expiring = row
        return (
            -int(prefers_expiring),
            missing_count,
            recipe.prep_minutes,
            -coverage,
            recipe.title,
        )

    scored.sort(key=sort_key)

    can_cook: list[RecipeOut] = []
    need_extra: list[RecipeOut] = []
    expiring_best: list[RecipeOut] = []

    for recipe, missing, uses_expiring, coverage, missing_count, _ in scored:
        out = RecipeOut(
            id=recipe.id,
            title=recipe.title,
            ingredients=recipe.ingredients,
            prep_minutes=recipe.prep_minutes,
            missing_from_pantry=missing,
            uses_expiring=sorted(set(uses_expiring)),
            pantry_coverage=round(coverage, 3),
        )
        if missing_count == 0:
            can_cook.append(out)
        elif 1 <= missing_count <= 2:
            need_extra.append(out)
        if uses_expiring and missing_count <= 2:
            expiring_best.append(out)

    note = (
        "Expired items are excluded from suggestions."
        if not include_expired
        else "Expired items are included because you asked for them."
    )

    return RecipeSuggestResponse(
        can_cook_now=can_cook[:12],
        need_one_or_two_items=need_extra[:12],
        best_for_expiring_soon=expiring_best[:12],
        pantry_note=note,
    )
