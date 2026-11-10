/**
 * Прокрутка колонки меню: подсвеченный пункт — в видимой части, у края — тень, пока за ним
 * есть пункты.
 *
 * Разбор экранов 10.11.2026: у админа 22 пункта, а на 1440 × 900 видно 15. «Домены рассылки»
 * продаж и вся группа «Настройки» стояли под краем колонки без признака прокрутки, и на
 * «Расходе», «Учётках», «Журнале действий» подсвеченного пункта видно не было.
 *
 * **Пункт приезжает в видимую часть при смене адреса — прокруткой самой колонки** (`scrollTop`),
 * а не `scrollIntoView`: тот двигает все прокрутки вокруг — и страницу, и колонку вбок, пока она
 * разворачивается и подписи в ней шире. Пункт, который и так виден, колонку не двигает: она не
 * уезжает из-под руки, выбравшей пункт. Сдвиг — до кадра (`useLayoutEffect`), а не на глазах.
 *
 * **Тень у края — пока за ним есть пункты** (`data-more-above`, `data-more-below` у
 * `.navScrollBox` в glass.css). Пункт приезжает с запасом на её высоту (`EDGE_ROOM`): под тенью
 * он читался бы хуже соседей.
 */

import { useLayoutEffect, useRef, useState } from 'react';

/** Высота тени у края колонки (`.navScrollBox` в glass.css) — и запас, с которым приезжает пункт. */
export const EDGE_ROOM = 16;

/** Есть ли пункты за верхним и нижним краем колонки. */
export interface Edges {
  above: boolean;
  below: boolean;
}

/** Края по прокрутке. Полпикселя — дробная прокрутка экрана с двойной плотностью: колонка,
 *  докрученная до конца, не должна показывать тень у края, за которым ничего нет. */
export function edgesOf(box: HTMLElement): Edges {
  return {
    above: box.scrollTop > 0.5,
    below: box.scrollTop + box.clientHeight < box.scrollHeight - 0.5,
  };
}

/** На сколько прокрутить колонку, чтобы пункт встал в неё целиком и не под тенью края; ноль —
 *  пункт виден. Отрицательное — вверх. */
export function revealShift(box: DOMRect, item: DOMRect, room = EDGE_ROOM): number {
  if (item.top < box.top + room) return item.top - box.top - room;
  if (item.bottom > box.bottom - room) return item.bottom - box.bottom + room;
  return 0;
}

/** Куда встать прокрутке после сдвига `shift` от `now`, в пределах от 0 до `max`. Едет к краю,
 *  и до него осталось меньше двух теней — до самого края: у края тени нет, и пункт там виден
 *  целиком. Без этого «Обзор» приезжал на 8 px ниже верха, и подпись его группы стояла под тенью. */
export function settled(now: number, shift: number, max: number, room = EDGE_ROOM): number {
  const end = Math.max(0, max);
  const next = now + shift;
  if (shift < 0 && next < 2 * room) return 0;
  if (shift > 0 && next > end - 2 * room) return end;
  return Math.min(Math.max(0, next), end);
}

/**
 * Колонка пунктов: ссылка на её прокрутку и края, за которыми есть пункты. `active` — ключ
 * подсвеченного пункта (`sections.activeKey`): сменился — пункт приезжает в видимую часть.
 * `items` — состав пунктов: сменился — края считаются заново; высоту колонки (она растёт
 * вслед за уехавшей шапкой, `shellLift.ts`) ловит `ResizeObserver`.
 */
export function useNavScroll(active: string | null, items: string) {
  const ref = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState<Edges>({ above: false, below: false });

  useLayoutEffect(() => {
    const box = ref.current;
    if (box === null) return undefined;
    const update = () => {
      const next = edgesOf(box);
      setEdges((was) => (was.above === next.above && was.below === next.below ? was : next));
    };
    update();
    box.addEventListener('scroll', update, { passive: true });
    const resize = new ResizeObserver(update);
    resize.observe(box);
    return () => {
      box.removeEventListener('scroll', update);
      resize.disconnect();
    };
  }, [items]);

  useLayoutEffect(() => {
    const box = ref.current;
    const item = box === null ? null : box.querySelector<HTMLElement>('[data-active]');
    if (box === null || item === null) return;
    const shift = revealShift(box.getBoundingClientRect(), item.getBoundingClientRect());
    if (shift === 0) return;
    box.scrollTop = settled(box.scrollTop, shift, box.scrollHeight - box.clientHeight);
  }, [active]);

  return { ref, edges };
}
