/**
 * Ключи сбора чипами — данные без отрисовки: чипы сметы и отчёта, снятые галочкой и их номера
 * строк, строка чисел под чипами.
 *
 * **Снимают номер строки, как её вписал человек** (`SegmentAsk.excluded`): сервер снимает ключ
 * после отсева, и «повтор строки N» у отсеянных не сдвигается. Номер взятого ключа называет
 * сервер (`keyword_lines`), строки он делит, как Python `str.splitlines` (`splitLines` — те же
 * разделители): иначе номер на экране и на сервере разошёлся бы на вставленном тексте с редким
 * переводом строки.
 *
 * **Правка поля ключей снятые не теряет**: снятый помнится текстом своей строки (`Dropped`), и
 * номер ищется заново по тексту поля — строка, вставленная выше, его не сдвигает. Строку снятого
 * ключа поправили или удалили — снятие пропало, и смета покажет ключ взятым.
 */

import { formatNumber } from '../format';
import type { SegmentEstimate, SegmentRunKeywords } from './segmentApi';

/** Ключ чипом: взят (покупается), снят человеком или отсеян сервером. */
export type KeyState = 'taken' | 'excluded' | 'rejected';

export interface KeyChip {
  phrase: string;
  state: KeyState;
  /** Слова рядом — пометка, «снят вами» или причина отсева, как их сказал сервер. */
  words?: string | undefined;
  /** Что вышло с покупкой выдачи — значками у взятого (отчёт). */
  marks?: string[] | undefined;
  /** Номер строки, как вписана: у ключа сметы — галочка. */
  line?: number | undefined;
}

/** Снятый ключ: номер строки, когда сняли, и её текст — по нему номер ищется после правки. */
export interface Dropped {
  line: number;
  content: string;
}

export const EXCLUDED_WORDS = 'снят вами';
export const EXCLUDED_AT_START = 'снят на запуске';
/** Снятые в строке чисел: в смете — человеком, в отчёте — на запуске. */
export const EXCLUDED_COUNT = 'снято вами';
export const EXCLUDED_AT_START_COUNT = 'снято на запуске';
export const LOST_MARK = 'выдача не пришла';
export const EMPTY_MARK = 'ничего не нашлось';

/** Разделители строк Python `str.splitlines`: так строки делит сервер. */
const LINE_BREAKS = new Set([
  '\n',
  '\r',
  '\v',
  '\f',
  '\u001c',
  '\u001d',
  '\u001e',
  '\u0085',
  '\u2028',
  '\u2029',
]);

/** Строки поля, как их делит сервер (`str.splitlines`): «\r\n» — один разрыв, за последним
 *  разрывом пустой строки нет. Пустые строки внутри — тоже строки: они держат номера. */
export function splitLines(text: string): string[] {
  const lines: string[] = [];
  let start = 0;
  for (let at = 0; at < text.length; at += 1) {
    const char = text.charAt(at);
    if (!LINE_BREAKS.has(char)) continue;
    lines.push(text.slice(start, at));
    if (char === '\r' && text.charAt(at + 1) === '\n') at += 1;
    start = at + 1;
  }
  if (start < text.length) lines.push(text.slice(start));
  return lines;
}

function line(text: string, number: number): string | undefined {
  return splitLines(text)[number - 1]?.trim();
}

/** Где сейчас строка снятого ключа: на прежнем месте, если её текст там, иначе — первая строка
 *  с тем же текстом; пропала — `null`. */
function placeOf(lines: string[], dropped: Dropped): number | null {
  if (lines[dropped.line - 1] === dropped.content) return dropped.line;
  const found = lines.indexOf(dropped.content);
  return found < 0 ? null : found + 1;
}

/** Номера строк снятых ключей в нынешнем тексте поля — по возрастанию, без повторов. */
export function excludedLines(text: string, dropped: readonly Dropped[]): number[] {
  const lines = splitLines(text).map((each) => each.trim());
  const found = new Set<number>();
  for (const each of dropped) {
    const place = placeOf(lines, each);
    if (place !== null) found.add(place);
  }
  return [...found].sort((a, b) => a - b);
}

/** Снять ключ строки `number` или вернуть ему галочку. */
export function toggled(
  text: string,
  dropped: readonly Dropped[],
  number: number,
  taken: boolean,
): Dropped[] {
  const lines = splitLines(text).map((each) => each.trim());
  const rest = dropped.filter((each) => placeOf(lines, each) !== number);
  if (taken) return rest;
  const content = line(text, number);
  return content === undefined ? rest : [...rest, { line: number, content }];
}

function joined(first: string, note: string | undefined): string {
  return note === undefined ? first : `${first}; ${note}`;
}

/** Чипы сметы: взятые и снятые — по порядку строк, как вписаны, затем отсеянные. `pending` —
 *  снятые на экране, пока смета не пересчитана; пусто — состояние сервера. */
export function estimateChips(
  found: SegmentEstimate,
  pending: ReadonlySet<number> | null,
): KeyChip[] {
  const lines = found.keyword_lines;
  const off = new Set(found.excluded);
  const chosen = [...found.keywords, ...found.excluded];
  const placed = chosen.filter((phrase) => lines[phrase] !== undefined);
  placed.sort((a, b) => (lines[a] ?? 0) - (lines[b] ?? 0));
  // Ключ без номера строки — у сервера так не бывает; он виден, но галочки у него нет.
  const loose = chosen.filter((phrase) => lines[phrase] === undefined);
  const chips: KeyChip[] = [...placed, ...loose].map((phrase) => {
    const number = lines[phrase];
    const dropped =
      pending !== null && number !== undefined ? pending.has(number) : off.has(phrase);
    return {
      phrase,
      line: number,
      state: dropped ? 'excluded' : 'taken',
      words: dropped ? joined(EXCLUDED_WORDS, found.notes[phrase]) : found.notes[phrase],
    };
  });
  return [...chips, ...rejectedChips(found.rejected)];
}

function rejectedChips(rejected: Record<string, string>): KeyChip[] {
  return Object.entries(rejected).map(([phrase, why]) => ({
    phrase,
    state: 'rejected' as const,
    words: why,
  }));
}

/** Чипы отчёта: взятые со значками покупки выдачи, снятые на запуске, отсеянные. Пропавший или
 *  пустой ключ, которого нет среди взятых, — своим чипом: молча он не пропадает. */
export function runChips(found: SegmentRunKeywords): KeyChip[] {
  const lost = new Set(found.lost);
  const empty = new Set(found.empty);
  const marks = (phrase: string): string[] => [
    ...(lost.has(phrase) ? [LOST_MARK] : []),
    ...(empty.has(phrase) ? [EMPTY_MARK] : []),
  ];
  const taken = new Set(found.taken);
  const stray = [...new Set([...found.lost, ...found.empty])].filter((each) => !taken.has(each));
  const bought: KeyChip[] = [...found.taken, ...stray].map((phrase) => ({
    phrase,
    state: 'taken',
    words: found.notes[phrase],
    marks: marks(phrase),
  }));
  const excluded: KeyChip[] = (found.excluded ?? []).map((phrase) => ({
    phrase,
    state: 'excluded',
    words: EXCLUDED_AT_START,
  }));
  return [...bought, ...excluded, ...rejectedChips(found.rejected)];
}

/** Строка чисел под чипами: сколько взято из всех, сняли, отсеяно; в отчёте — сколько ключей
 *  вышло без выдачи. Нулей «снято» и «отсеяно» нет: место держит сама строка — она под чипами. */
export function keysLine(chips: readonly KeyChip[], excludedWords: string): string {
  const count = (state: KeyState) => chips.filter((chip) => chip.state === state).length;
  const marked = (mark: string) => chips.filter((chip) => chip.marks?.includes(mark)).length;
  const parts = [`взято ${formatNumber(count('taken'))} из ${formatNumber(chips.length)}`];
  if (count('excluded') > 0) parts.push(`${excludedWords} ${formatNumber(count('excluded'))}`);
  if (count('rejected') > 0) parts.push(`отсеяно ${formatNumber(count('rejected'))}`);
  if (marked(LOST_MARK) > 0) {
    parts.push(`${LOST_MARK} ${formatNumber(marked(LOST_MARK))} — заплачено, а провайдер не отдал`);
  }
  if (marked(EMPTY_MARK) > 0) parts.push(`${EMPTY_MARK} ${formatNumber(marked(EMPTY_MARK))}`);
  const text = parts.join(' · ');
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}.`;
}
