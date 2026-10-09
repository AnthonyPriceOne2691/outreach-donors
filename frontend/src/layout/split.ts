/**
 * Список и открытая запись рядом — на широком окне.
 *
 * Замечание Anthony 09.10.2026 про «Диалоги»: «много места пустого — сдвинуть шапку и
 * таблицу влево, сократить, а справа сразу отображать диалог». Так устроены почта
 * и переписка у Outlook, Gmail, Intercom, Front: список — узкой колонкой постоянной
 * ширины, запись — остатком. Две колонки — от `lg` Mantine (75em, 1 200 px окна): уже
 * этого переписке за вычетом меню и списка остаётся меньше 600 px, и узкое окно
 * открывает запись отдельной страницей, как раньше.
 */

import { useMediaQuery } from '@mantine/hooks';

export const SPLIT_QUERY = '(min-width: 75em)';

/** Разделы, у которых список стоит рядом с записью. */
const SPLIT_SECTIONS = ['/threads'];

export function useSplit(): boolean {
  // Значение — с первой отрисовки, а не после неё: иначе широкое окно на миг
  // рисовало бы узкий вид и перестраивалось на глазах.
  return useMediaQuery(SPLIT_QUERY, false, { getInitialValueInEffect: false }) === true;
}

/**
 * Ключ рабочей области рамы (`layout/Shell`). По пути — чтобы подъём играл на
 * каждом экране; у раздела со списком рядом с записью — по разделу: иначе каждый
 * щелчок по строке пересоздавал бы список, сбрасывал его прокрутку и заново
 * проигрывал подъём всего экрана.
 */
export function workKey(pathname: string, split: boolean): string {
  if (!split) return pathname;
  const section = SPLIT_SECTIONS.find(
    (root) => pathname === root || pathname.startsWith(`${root}/`),
  );
  return section ?? pathname;
}
