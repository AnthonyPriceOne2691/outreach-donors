/**
 * Обход 24/7 словами — блок на вкладке «Обход доноров» (`CrawlAlways`) и строка донора
 * (`CrawlDepth`), 17.11.2026.
 *
 * Строку состояния и строки «i» блока пишет сервер (`words`, `details`); здесь — то, что экран
 * считает сам: сколько сайтов получат пометку при пороге в поле (по долям `yields`, без запроса),
 * проценты с той точностью, при которой число не перескакивает порог (`docs/UI_RULES.md`), и что
 * обход 24/7 сделал с донором.
 */

import type { AlwaysChange, AlwaysView, DonorDepth, WeakMark } from '../api/crawlAlways';
import { formatDateTime, formatNumber, formatPercent, plural } from '../format';
import { pagesOf } from './crawlWords';

/** Часть 24/7 — страниц сайта за проход: после первой сайт получает или не получает пометку.
 *  Число — сервера (`part_pages`); это — только на случай сервера старше экрана. */
export const PART_PAGES = 10_000;

/** Часть 24/7 — числом сервера, а без него — своим. */
export function partPages(view: AlwaysView): number {
  return view.part_pages ?? PART_PAGES;
}

/** Что делает включение — до нажатия, у кнопки (`ConfirmPopover`). `part` — часть 24/7. */
export function startWarning(part: number): string {
  return (
    'Обход пойдёт сам вне ночного окна: сначала сайты, которых ещё не обходили,\u00a0— до ' +
    `${formatNumber(1000)} страниц, потом по ${formatNumber(part)} страниц каждого сайта, ` +
    `дальше\u00a0— следующие ${formatNumber(part)}, первыми сайты с большей долей платных ` +
    'ссылок. Ночной обход с полуночи по Москве остаётся как есть. Юниты Ahrefs и месячный ' +
    'потолок платного обхода действуют как сейчас. Пауза\u00a0— одним нажатием.'
  );
}

/** Рубильник окружения выключен — одной строкой, без кнопок. */
export const UNAVAILABLE =
  'Обход 24/7 выключен настройкой сервиса: доноров обходят ночной обход и кнопки.';

/** Пока 24/7 включён и не всё пройдено, строка перечитывается сама. */
export function watched(view: AlwaysView | undefined): boolean {
  return view !== undefined && view.available && view.enabled && view.done_at === null;
}

/** Не встаёт ли число на ноль или по другую сторону порога при `digits` знаках. */
function misleads(value: number, digits: number, threshold: number | undefined): boolean {
  const shown = Math.round(value * 10 ** digits) / 10 ** digits;
  if (value > 0 && shown === 0) return true;
  return threshold !== undefined && value < threshold !== shown < threshold;
}

/** Процент из процентов: 0.04 → «0,04%». Знаков — сколько нужно, чтобы доля больше нуля не
 *  встала на ноль, а доля у порога не перескочила его: 0,0999 при пороге 0,1 — «0,0999%». */
export function percentText(value: number, threshold?: number): string {
  let digits = 2;
  while (digits < 4 && misleads(value, digits, threshold)) digits += 1;
  return formatPercent(value / 100, digits);
}

/** Сколько сайтов получат пометку при пороге: доля строго меньше порога. */
export function markedCount(yields: readonly number[], threshold: number): number {
  return yields.filter((share) => share < threshold).length;
}

/** Строка под полем порога: «при этом пороге пометку получат 102 из 155 сайтов». Считает экран
 *  по долям сервера — сайты, прошедшие первую часть (`part` страниц). */
export function markWords(yields: readonly number[], threshold: number, part: number): string {
  const total = yields.length;
  if (total === 0) {
    return `пометку получат сайты, прошедшие первые ${pagesOf(part)}, — таких пока нет`;
  }
  const marked = markedCount(yields, threshold);
  const verb = plural(marked, 'получит', 'получат', 'получат');
  const sites = plural(total, 'сайта', 'сайтов', 'сайтов');
  const of = `${formatNumber(marked)} из ${formatNumber(total)}\u00a0${sites}`;
  return `при этом пороге пометку ${verb} ${of}`;
}

/** Почему включённый 24/7 сейчас не идёт — словами сервера. */
export function waitingWords(view: AlwaysView): string | null {
  if (view.waiting_for === null) return null;
  return `Сейчас не идёт: ${view.waiting_for.replace(/\.$/, '')}.`;
}

/** Когда пройдено всё. */
export function doneWords(view: AlwaysView): string | null {
  if (view.done_at === null) return null;
  return `Все сайты пройдены до конца: ${formatDateTime(view.done_at)}.`;
}

/** «12 400 страниц» — именительный. */
function pagesWords(count: number): string {
  return `${formatNumber(count)}\u00a0${plural(count, 'страница', 'страницы', 'страниц')}`;
}

/** Строки «i» блока: слова сервера, сколько пройдено и кто менял настройки последним. */
export function alwaysHint(view: AlwaysView): string[] {
  const lines = [...view.details];
  if (view.pages > 0) lines.push(`Пройдено обходом 24/7: ${pagesWords(view.pages)}.`);
  if (view.set_at !== null) {
    const who = view.set_by === null ? '' : `, ${view.set_by}`;
    lines.push(`Последняя смена настроек: ${formatDateTime(view.set_at)}${who}.`);
  }
  return lines;
}

/** Чего не удалось сменить — заголовком отказа. */
export function changeTitle(change: AlwaysChange | undefined): string {
  if (change?.enabled !== undefined) {
    return change.enabled ? 'Обход 24/7 не запущен' : 'Обход 24/7 не поставлен на паузу';
  }
  if (change?.threshold_pct !== undefined) return 'Порог не сохранён';
  return 'Переобход слабых не переключён';
}

/** Есть ли что сказать о доноре: 24/7 его обходил, обходит или у него пометка. Сайт, до которого
 *  24/7 ещё не дошёл, строки не получает — «24/7: 0 страниц» в каждой строке было бы шумом. */
export function depthShown(depth: DonorDepth): boolean {
  return depth.pages > 0 || depth.running || depth.weak || depth.weak_by !== null;
}

/** Строка донора: «24/7: 12 400 страниц», «24/7 идёт: …», «… — весь сайт». */
export function depthWords(depth: DonorDepth): string {
  if (depth.running) return `24/7 идёт: ${pagesWords(depth.pages)}`;
  if (depth.exhausted) return `24/7: ${pagesWords(depth.pages)} — весь сайт`;
  return `24/7: ${pagesWords(depth.pages)}`;
}

/** Часть идёт или сайт пройден до конца — хвостом строки частей. */
function partsTail({ running, exhausted }: DonorDepth): string {
  if (running) return ' — часть идёт сейчас';
  return exhausted ? ' — сайт пройден до конца' : '';
}

/** «Обход 24/7 прошёл 25 431 страницу за 3 части» — винительный: после «прошёл» и «за». */
function partsLine(depth: DonorDepth): string {
  const { pages, parts } = depth;
  const passed = `${formatNumber(pages)}\u00a0${plural(pages, 'страницу', 'страницы', 'страниц')}`;
  const count = `${formatNumber(parts)} ${plural(parts, 'часть', 'части', 'частей')}`;
  return `Обход 24/7 прошёл ${passed} за ${count}${partsTail(depth)}.`;
}

/** «Платная ссылка на 4 из 10 000 страниц — 0,04%». Страниц с платной ссылкой — числом сервера
 *  (`selling_pages`); сервер старше или числа нет — от доли. */
function shareLine(depth: DonorDepth, threshold?: number): string | null {
  const { pages, yield_pct: share } = depth;
  if (share === null) return null;
  const paid = depth.selling_pages ?? Math.round((share * pages) / 100);
  const shown = percentText(share, threshold);
  return `Платная ссылка на ${formatNumber(paid)} из ${pagesOf(pages)} — ${shown}.`;
}

const AUTO_MARKED =
  'Пометил сервис: доля ниже порога. 24/7 не ведёт сайт дальше, пока не включён переобход слабых.';
const HUMAN_MARKED =
  'Пометил человек — порог сервиса к сайту не применяется. 24/7 не ведёт его дальше, пока не ' +
  'включён переобход слабых.';
const HUMAN_CLEARED = 'Пометку снял человек — сайт идёт дальше при любом пороге.';

/** Кто решил: сервис по порогу или человек — пометил или снял. */
function whoLine({ weak, weak_by: by }: DonorDepth): string | null {
  if (by === 'human') return weak ? HUMAN_MARKED : HUMAN_CLEARED;
  return weak ? AUTO_MARKED : null;
}

/** Оговорка сервера — предложением: с заглавной и с точкой. */
function noteLine({ note }: DonorDepth): string | null {
  const said = note?.trim().replace(/\.$/, '');
  if (!said) return null;
  return `${said.charAt(0).toUpperCase()}${said.slice(1)}.`;
}

/** Строки «i» у донора. Порог — чтобы доля у его края не перескочила его в словах. */
export function depthLines(depth: DonorDepth, threshold?: number): string[] {
  return [partsLine(depth), shareLine(depth, threshold), whoLine(depth), noteLine(depth)].filter(
    (line) => line !== null,
  );
}

/** Кнопка пометки руками: подпись и что уходит на сервер. */
export interface MarkAction {
  label: string;
  weak: WeakMark;
}

/** Что можно сделать с пометкой: снять или поставить; решил человек — ещё и вернуть сервису. */
export function markActions({ weak, weak_by: by }: DonorDepth): MarkAction[] {
  const flip: MarkAction = weak
    ? { label: 'Снять пометку', weak: false }
    : { label: 'Пометить', weak: true };
  return by === 'human' ? [flip, { label: 'Вернуть оценку сервису', weak: null }] : [flip];
}
