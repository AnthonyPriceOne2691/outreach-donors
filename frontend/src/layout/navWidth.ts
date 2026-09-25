/**
 * Ширина боковой колонки — по самому длинному пункту меню.
 *
 * Замечание 25.09.2026: колонка была шире нужного — справа от самого
 * длинного пункта оставалось 59 px при 24 слева. Теперь справа от него
 * столько же, сколько слева от всех.
 *
 * Ширина считается по тексту, а не задаётся числом. Шрифт у людей разный
 * (SF на маке, Segoe на Windows), набор пунктов зависит от прав, а раздел
 * с длинным именем, добавленный потом, при зашитом числе молча обрезался бы
 * многоточием: подпись пункта не переносится.
 *
 * Меряет холст, а не разметка: ширина нужна до первого кадра. Поправка
 * после него сдвинула бы рабочую область на глазах — у неё переход
 * по отступу.
 */

import { FONT_STACK } from '../theme';

/** Поле колонки — `p="sm"` у `AppShell.Navbar`. */
const NAV_PADDING = 12;

/** От края рамки до текста пункта: кромка рамки 1 + её поле 10 + кромка
 *  строки 1 + поле пункта 12. Замерено в браузере: 24 px. */
const TEXT_INSET = 24;

/** Подпись пункта — размер `sm` Mantine. Число, а не rem: ширину Mantine
 *  сам переводит в rem, и при крупном шрифте системы колонка растёт вместе
 *  с подписями. */
const LABEL_FONT = `14px ${FONT_STACK}`;

/** Уже не бывает: ниже в рамке не помещается переключатель тем. */
const MIN_WIDTH = 170;

/** Холста нет (тесты в jsdom) — прежняя ширина. */
export const FALLBACK_WIDTH = 232;

export function navbarWidth(labels: readonly string[]): number {
  const widest = widestLabel(labels);
  if (widest === null) return FALLBACK_WIDTH;
  // Вверх, а не до ближайшего: подпись, которой не хватило доли пикселя,
  // обрезается многоточием целиком.
  return Math.max(MIN_WIDTH, Math.ceil(widest) + 2 * (TEXT_INSET + NAV_PADDING));
}

function widestLabel(labels: readonly string[]): number | null {
  const context = document.createElement('canvas').getContext('2d');
  if (context === null || labels.length === 0) return null;
  context.font = LABEL_FONT;
  return Math.max(...labels.map((label) => context.measureText(label).width));
}
