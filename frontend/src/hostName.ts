/**
 * Имя хоста на экране — как его пишут люди: «пример.рф», а не «xn--e1afmkfd.xn--p1ai».
 *
 * 18.11.2026: база хранит имена хостов в ASCII (IDNA, punycode), и экраны печатали `xn--…`
 * там, где человек ждёт кириллицу, — домен не узнавали ни глазом, ни поиском. Раскодирование —
 * одним местом на весь фронт: швы имён (`components/Seams`) и строки, собранные из хоста, зовут
 * его, а не каждый экран своё. Меняется только то, что читают глазами: ссылка (`href`), формы
 * и запросы к серверу получают имя как было, в ASCII; выделенное и скопированное с экрана —
 * таким, каким видно.
 *
 * Свой разбор, а не пакет: нужен один разбор Punycode (RFC 3492, раздел 6.2), без кодирования
 * и таблиц IDNA, а новых зависимостей фронт не берёт (договорённость 18.11.2026). `URL`
 * браузера переводит только в обратную сторону — в ASCII.
 *
 * Чего не раскодировать — показать как есть: `xn--…` лучше, чем чужое имя или половина имени.
 *  - Сбой разбора (не цифра, обрыв, переполнение) — вся строка как есть.
 *  - Строка не имя хоста — адрес страницы, фраза («/», «:», пробел): половина раскодированного
 *    адреса читалась бы хуже целого ASCII.
 *  - Метка раскодировалась в одно ASCII («xn--abc-» → «abc») — это другое имя на вид, а не IDN.
 *  - В метке знак, которого в имени не бывает (управляющий, смена направления, «．»), или
 *    латиница вперемешку с кириллицей или греческим («exаmple» с кириллической «а»): так
 *    подделывают знакомое имя, а имена бывают и чужие — адрес отправителя письма. Подделку
 *    целиком одной письменностью («рау» вместо «pay») не ловит и это: ссылка ведёт по ASCII.
 */

/** Параметры Punycode — RFC 3492, раздел 5. */
const BASE = 36;
const T_MIN = 1;
const T_MAX = 26;
const SKEW = 38;
const DAMP = 700;
const INITIAL_BIAS = 72;
const INITIAL_N = 0x80;
/** Потолок счёта — 32-битное целое со знаком, как у разбора в RFC (раздел 6.4): дальше —
 *  переполнение и сбой, а не потеря точности. */
const MAX_INT = 0x7fffffff;
/** Длиннее метки в DNS не бывает (RFC 1034): длинная строка — не имя, и разбирать её незачем. */
const MAX_LABEL = 63;
const ACE_PREFIX = 'xn--';

/** Знак ASCII, которого в имени хоста нет: «/», «:», «@», пробел. Не-ASCII не мешает — имя
 *  бывает уже юникодным. */
const NOT_HOST = /[^\w.\u0080-\u{10ffff}-]/u;
/** Раскодированная метка — только буквы, знаки при буквах, цифры и дефис. */
const LABEL_TEXT = /^[\p{L}\p{M}\p{N}-]+$/u;
const NON_ASCII = /[\u0080-\u{10ffff}]/u;
const LATIN = /\p{Script=Latin}/u;
const LOOKALIKE = /[\p{Script=Cyrillic}\p{Script=Greek}]/u;

/** Значение цифры (раздел 5): «a»–«z» в любом регистре — 0–25, «0»–«9» — 26–35; не цифра —
 *  `BASE`. */
function digitOf(code: number): number {
  if (code >= 0x30 && code <= 0x39) return code - 0x16;
  if (code >= 0x41 && code <= 0x5a) return code - 0x41;
  if (code >= 0x61 && code <= 0x7a) return code - 0x61;
  return BASE;
}

/** Порог цифры на шаге `k` (раздел 6.2). */
function thresholdAt(k: number, bias: number): number {
  if (k <= bias) return T_MIN;
  if (k >= bias + T_MAX) return T_MAX;
  return k - bias;
}

/** Новый сдвиг после вставки (раздел 6.1). */
function adapt(delta: number, points: number, first: boolean): number {
  let scaled = Math.floor(delta / (first ? DAMP : 2));
  scaled += Math.floor(scaled / points);
  let k = 0;
  while (scaled > ((BASE - T_MIN) * T_MAX) / 2) {
    scaled = Math.floor(scaled / (BASE - T_MIN));
    k += BASE;
  }
  return k + Math.floor(((BASE - T_MIN + 1) * scaled) / (scaled + SKEW));
}

/** Число переменной длины с позиции `at` (раздел 3.3), прибавленное к `start`: новое значение
 *  и позиция за числом; `null` — обрыв, не цифра или переполнение. */
function readDelta(
  digits: string,
  at: number,
  start: number,
  bias: number,
): [number, number] | null {
  let value = start;
  let weight = 1;
  for (let position = at, k = BASE; position < digits.length; position += 1, k += BASE) {
    const digit = digitOf(digits.charCodeAt(position));
    if (digit >= BASE || digit * weight > MAX_INT - value) return null;
    value += digit * weight;
    const threshold = thresholdAt(k, bias);
    if (digit < threshold) return [value, position + 1];
    weight *= BASE - threshold;
    if (weight > MAX_INT) return null;
  }
  return null;
}

/** Знак, который можно вставить: за пределом Юникода и половинки суррогатной пары — нет. */
function insertable(point: number): boolean {
  return point <= 0x10ffff && (point < 0xd800 || point > 0xdfff);
}

/** Разбор Punycode — метка без «xn--» (раздел 6.2): ASCII до последнего дефиса — как есть,
 *  после — вставки; `null` — сбой. Наружу — ради векторов RFC в тесте: экран зовёт
 *  `shownHost`. */
export function decodePunycode(encoded: string): string | null {
  const cut = Math.max(encoded.lastIndexOf('-'), 0);
  const basic = encoded.slice(0, cut);
  if (NON_ASCII.test(basic)) return null;
  const output = Array.from(basic, (char) => char.charCodeAt(0));
  let point = INITIAL_N;
  let bias = INITIAL_BIAS;
  let index = 0;
  for (let at = cut > 0 ? cut + 1 : 0; at < encoded.length; ) {
    const read = readDelta(encoded, at, index, bias);
    if (read === null) return null;
    const [next, after] = read;
    const points = output.length + 1;
    bias = adapt(next - index, points, index === 0);
    point += Math.floor(next / points);
    if (!insertable(point)) return null;
    const place = next % points;
    output.splice(place, 0, point);
    index = place + 1;
    at = after;
  }
  return String.fromCodePoint(...output);
}

/** Метку можно показать вместо ASCII — список «не раскодировать» в шапке. */
function showable(label: string): boolean {
  return (
    LABEL_TEXT.test(label) && NON_ASCII.test(label) && !(LATIN.test(label) && LOOKALIKE.test(label))
  );
}

/** Метка на экран: `xn--…` — раскодированной, прочие — как есть; `null` — не показать. */
function shownLabel(label: string): string | null {
  if (label.slice(0, ACE_PREFIX.length).toLowerCase() !== ACE_PREFIX) return label;
  if (label.length > MAX_LABEL) return null;
  const decoded = decodePunycode(label.slice(ACE_PREFIX.length));
  return decoded !== null && showable(decoded) ? decoded : null;
}

/**
 * Хост для глаз: «xn--e1afmkfd.xn--p1ai» — «пример.рф». Каждая метка `xn--…` (в любом
 * регистре) раскодирована, прочие — как были, без смены регистра; не вышло хоть с одной — вся
 * строка как есть. Пустое и `null` — как есть.
 */
export function shownHost(host: string): string;
export function shownHost(host: string | null): string | null;
export function shownHost(host: string | null): string | null {
  if (!host || !host.toLowerCase().includes(ACE_PREFIX) || NOT_HOST.test(host)) return host;
  const labels = host.split('.').map(shownLabel);
  return labels.includes(null) ? host : labels.join('.');
}

/**
 * Адрес почты для глаз: раскодирован только домен — часть после последней «@»; имя ящика
 * до неё — как есть, это не хост. Без «@» строка — имя хоста: в одной колонке стоп-листа
 * бывает и домен, и адрес.
 */
export function shownAddress(email: string): string;
export function shownAddress(email: string | null): string | null;
export function shownAddress(email: string | null): string | null {
  if (!email) return email;
  const at = email.lastIndexOf('@') + 1;
  return email.slice(0, at) + shownHost(email.slice(at));
}

/**
 * Имя — домен или адрес — содержит искомое без учёта регистра, в записи базы или в той, что
 * на экране: человек набирает «пример», а в данных `xn--e1afmkfd`.
 */
export function nameIncludes(name: string, needle: string): boolean {
  const sought = needle.toLowerCase();
  return [name, shownAddress(name)].some((form) => form.toLowerCase().includes(sought));
}
