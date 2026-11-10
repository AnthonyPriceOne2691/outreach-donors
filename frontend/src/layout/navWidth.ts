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

/** Значок пункта (`sections.ts`): 20 px и зазор 12 до подписи — отступ левой части
 *  `NavLink` (10.10.2026, у пунктов появились значки). */
export const ICON_ROOM = 32;

/** Уже не бывает: ниже в рамке не помещается переключатель тем. */
const MIN_WIDTH = 170;

/** Холста нет (тесты в jsdom) — прежняя ширина. */
export const FALLBACK_WIDTH = 232;

/** Больше значок числа ждущей работы не показывает (`NavMenu`): четырёхзначное наезжало на
 *  подпись. Точное число — на «Обзоре» и на экране раздела. */
export const COUNT_CAP = 999;

/** Самый широкий значок числа — сверх потолка. */
export const COUNT_OVER = `${COUNT_CAP}+`;

/** Значок числа — `Badge size="sm"` Mantine: 10 px жирным, разрядка 0,25 px на знак. */
const COUNT_FONT = `700 10px ${FONT_STACK}`;
const COUNT_SPACING = 0.25;

/** Вокруг подписи значка — поля по 8 px и кромка по 1 px, от подписи пункта его отделяет поле
 *  правой части `NavLink`, 12 px. */
export const COUNT_FRAME = 2 * (8 + 1) + 12;

/** Подпись группы — `.navGroupTitle` в glass.css: 11 px жирным, заглавными, разрядка
 *  0,08em на каждую букву. */
const GROUP_FONT = `700 11px ${FONT_STACK}`;
const GROUP_SPACING = 0.08 * 11;

/** От края рамки до подписи группы: кромка рамки 1 + её поле 10 + поле подписи 12. */
export const GROUP_INSET = 23;

/** Линия после подписи до края колонки: зазор 8 и хотя бы 16 px самой линии — без неё
 *  подпись читается текстом, а не границей группы. */
export const GROUP_LINE = 24;

/** `counted` — подписи пунктов, у которых бывает число ждущей работы; `groups` — подписи
 *  групп. Подпись группы тоже не переносится: в две строки она читалась бы двумя группами
 *  (с 11.10 по 23.10.2026 колонку держала «Доноры и рекламодатели»). */
export function navbarWidth(
  labels: readonly string[],
  counted: readonly string[] = [],
  groups: readonly string[] = [],
): number {
  const context = document.createElement('canvas').getContext('2d');
  if (context === null || labels.length === 0) return FALLBACK_WIDTH;
  const widest = widestLabel(context, labels, counted);
  // Вверх, а не до ближайшего: подпись, которой не хватило доли пикселя,
  // обрезается многоточием целиком.
  const byItems = Math.ceil(widest) + ICON_ROOM + 2 * (TEXT_INSET + NAV_PADDING);
  const byGroups =
    Math.ceil(widestGroup(context, groups)) + GROUP_LINE + 2 * (GROUP_INSET + NAV_PADDING);
  return Math.max(MIN_WIDTH, byItems, groups.length > 0 ? byGroups : 0);
}

/**
 * Место под число справа от подписи (`layout/work.ts`) — по самому широкому значку, «999+», тем же
 * холстом, что подписи. Держится у такого пункта всегда, а не когда число пришло: иначе колонка
 * дёргалась бы с ответом. До 10.11.2026 место было числом — 48 px, а значку с полем нужно 59:
 * «Рекламодатели» рядом с «999+» резались до «Рекламодате…» (разбор экранов 10.11.2026).
 */
function countRoom(context: CanvasRenderingContext2D): number {
  context.font = COUNT_FONT;
  return context.measureText(COUNT_OVER).width + COUNT_OVER.length * COUNT_SPACING + COUNT_FRAME;
}

function widestLabel(
  context: CanvasRenderingContext2D,
  labels: readonly string[],
  counted: readonly string[],
): number {
  const count = countRoom(context);
  context.font = LABEL_FONT;
  const room = (label: string) => (counted.includes(label) ? count : 0);
  return Math.max(...labels.map((label) => context.measureText(label).width + room(label)));
}

function widestGroup(context: CanvasRenderingContext2D, groups: readonly string[]): number {
  context.font = GROUP_FONT;
  const width = (title: string) =>
    context.measureText(title.toUpperCase()).width + title.length * GROUP_SPACING;
  return Math.max(0, ...groups.map(width));
}
