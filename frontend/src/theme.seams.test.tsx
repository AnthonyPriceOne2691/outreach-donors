/**
 * Ревью стыков (F2): заголовок жёлтой плашки (`Alert color="yellow"`) на светлой теме.
 *
 * Янтарь заголовка (`--status-amber-ink`) на янтарной подложке (`--mantine-color-yellow-light`)
 * намерен 4,26–4,39 : 1 при норме 4,5 (замер «Продаж», `scripts/ui_contrast.py`; BACKLOG,
 * «Контраст (замер «Продаж»)»). Продажи обходили это у себя — `INK_TITLE` в трёх файлах
 * (`SenderPane`, `QueuePane`, `ChainLetter`), а жёлтые плашки остальных экранов (и двух экранов
 * загрузки продаж) остались янтарными. Ошибка общая — чинится один раз, в теме, тем же приёмом,
 * что заголовок уведомления (`theme.ts`, `Notification`): заголовок чернилами, смысл несёт
 * подложка. После правки темы копии `INK_TITLE` в продажах убираются.
 *
 * Правка — в теме (`theme.ts`, `Alert`) для плашки любого цвета. Каскад jsdom не считает —
 * проверяется стиль, который ставит тема; число — замером `scripts/ui_contrast.py` (экран
 * `letters-off`, обе темы).
 */

import { Alert } from '@mantine/core';
import { describe, expect, it } from 'vitest';

import { renderWith } from './test/render';

function titleOf(container: HTMLElement): HTMLElement {
  const title = container.querySelector<HTMLElement>('.mantine-Alert-title');
  if (title === null) throw new Error('у плашки нет заголовка');
  return title;
}

describe('жёлтая плашка (F2)', () => {
  it('заголовок — чернилами темы, а не янтарём на янтаре', () => {
    const { container } = renderWith(
      <Alert color="yellow" title="Отправлять нечем">
        Все домены выключены.
      </Alert>,
    );

    expect(titleOf(container).style.color).toBe('var(--ink)');
  });

  it.each(['green', 'red', 'blue'])('у плашки цвета %s — те же чернила темы', (color) => {
    const { container } = renderWith(
      <Alert color={color} title="Заголовок плашки">
        Пояснение.
      </Alert>,
    );

    expect(titleOf(container).style.color).toBe('var(--ink)');
  });

  it('приём продаж (`INK_TITLE`) ставит тот же стиль — проверка, на которой стоит тест выше', () => {
    const { container } = renderWith(
      <Alert
        color="yellow"
        title="Продажи к почте не подключены"
        styles={{ title: { color: 'var(--ink)' } }}
      >
        Письма не соберутся.
      </Alert>,
    );

    expect(titleOf(container).style.color).toBe('var(--ink)');
  });
});
