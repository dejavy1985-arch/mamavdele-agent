"""Маршрутизатор: определяет, к какому проекту относится запрос.

Стратегия этапа 1 намеренно простая, прозрачная и офлайновая: сопоставление по
ключевым словам и псевдонимам из манифестов проектов, со счётом совпадений.
Это предсказуемо и легко тестируется, без обращения к внешней модели.

Возможные исходы:
  MATCH      единственный уверенный кандидат;
  AMBIGUOUS  несколько кандидатов близки, нужно уточнение у пользователя;
  UNKNOWN    ни один проект не подходит.

Позже сюда можно добавить LLM-классификатор как второй слой, не ломая интерфейс.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from .registry import Project

MATCH = "match"
AMBIGUOUS = "ambiguous"
UNKNOWN = "unknown"


@dataclass
class RouteCandidate:
    project_id: str
    title: str
    score: int
    hits: List[str] = field(default_factory=list)


@dataclass
class RouteResult:
    outcome: str
    candidates: List[RouteCandidate] = field(default_factory=list)

    @property
    def best(self) -> Optional[RouteCandidate]:
        return self.candidates[0] if self.candidates else None


def _tokenize(text: str) -> List[str]:
    # Слова из букв/цифр в нижнем регистре (русские и латинские).
    return re.findall(r"[\w]+", (text or "").lower(), flags=re.UNICODE)


def score_project(text: str, tokens: List[str], project: Project) -> RouteCandidate:
    text_l = (text or "").lower()
    hits: List[str] = []
    score = 0

    # Псевдоним весит больше: это прямое указание на проект.
    for alias in project.aliases:
        if alias and alias in text_l:
            score += 5
            hits.append(alias)

    # id проекта, названный прямо, тоже сильный сигнал.
    if project.id.lower() in text_l:
        score += 5
        hits.append(project.id.lower())

    # Ключевые слова: точное совпадение токена или вхождение фразы.
    token_set = set(tokens)
    for kw in project.keywords:
        if not kw:
            continue
        if " " in kw:
            if kw in text_l:
                score += 2
                hits.append(kw)
        elif kw in token_set:
            score += 2
            hits.append(kw)

    return RouteCandidate(
        project_id=project.id, title=project.title, score=score, hits=hits
    )


def route(text: str, projects: List[Project], margin: int = 2) -> RouteResult:
    """Определить проект по тексту запроса.

    margin: минимальный отрыв лидера от второго кандидата, чтобы считать выбор
    уверенным. При меньшем отрыве возвращаем AMBIGUOUS для уточнения.
    """
    tokens = _tokenize(text)
    scored = [score_project(text, tokens, p) for p in projects]
    scored = [c for c in scored if c.score > 0]
    scored.sort(key=lambda c: c.score, reverse=True)

    if not scored:
        return RouteResult(outcome=UNKNOWN, candidates=[])

    if len(scored) == 1:
        return RouteResult(outcome=MATCH, candidates=scored)

    top, second = scored[0], scored[1]
    if top.score - second.score >= margin:
        return RouteResult(outcome=MATCH, candidates=scored)

    # Лидеры близки: показываем всех, у кого счёт совпал с максимумом.
    close = [c for c in scored if top.score - c.score < margin]
    return RouteResult(outcome=AMBIGUOUS, candidates=close)
