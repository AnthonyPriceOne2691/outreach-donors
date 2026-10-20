/**
 * Поле «Примеры сайтов» до нажатия: сколько сайтов, сколько повторов и что не адрес.
 *
 * Последнее слово — у сервера (`keywords/examples.py`): он кодирует имя по IDNA и знает
 * служебные зоны. Здесь — то, что видно сразу и стоит сказать до запроса: строка
 * с пробелом, чужая схема, имя без зоны, адрес IP и больше десяти сайтов. Слова —
 * те же, что у сервера: один и тот же отказ не должен звучать двумя голосами.
 */

import { plural } from '../format';

/** Потолок сайтов — тот же, что у сервера (`examples.MAX_EXAMPLES`). */
export const MAX_EXAMPLES = 10;

const SCHEME = /^([a-z][a-z0-9+.-]*):\/\//i;
const IPV4 = /^\d{1,3}(?:\.\d{1,3}){3}$/;

/** Имя сайта из строки, как его сравнивает сервер: без схемы, пути, порта и `www.`. */
export function siteName(line: string): string {
  const rest = line.trim().replace(SCHEME, '');
  const authority = rest.split(/[/?#]/, 1)[0] ?? '';
  const host = authority.slice(authority.lastIndexOf('@') + 1).replace(/:\d*$/, '');
  return host
    .toLowerCase()
    .replace(/\.$/, '')
    .replace(/^www\./, '');
}

/** Почему строка не адрес сайта — словами сервера; `null` — похожа на адрес. */
export function lineProblem(line: string): string | null {
  const text = line.trim();
  if (/\s/.test(text)) return 'пробел внутри — в строке должен быть один адрес';
  const scheme = SCHEME.exec(text)?.[1];
  if (scheme !== undefined && !/^https?$/i.test(scheme)) {
    return `схема «${scheme}» — открываем только http и https`;
  }
  const name = siteName(text);
  if (name === '' || name.startsWith('['))
    return 'нет имени сайта — в строке только схема или путь';
  if (IPV4.test(name)) return 'это адрес IP, а не имя сайта';
  if (!name.includes('.')) return 'нет зоны — имя сайта пишется с точкой, как example.com';
  return null;
}

export interface ExamplesCount {
  /** Сайтов без повторов. */
  sites: number;
  /** Строк, повторивших уже вписанный сайт. */
  repeats: number;
}

/** Сколько сайтов в поле и сколько повторов — тем же сравнением имён, что у сервера. */
export function countExamples(text: string): ExamplesCount {
  const names = text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line !== '' && lineProblem(line) === null)
    .map(siteName);
  const sites = new Set(names).size;
  return { sites, repeats: names.length - sites };
}

/** Почему поле нельзя отправить — словами сервера; `null` — можно (или поле пусто). */
export function examplesRefusal(text: string): string | null {
  const problems = text
    .split('\n')
    .map((line, index) => ({ line: line.trim(), number: index + 1 }))
    .filter(({ line }) => line !== '')
    .flatMap(({ line, number }) => {
      const problem = lineProblem(line);
      return problem === null ? [] : [`строка ${number}: «${line.slice(0, 80)}» — ${problem}`];
    });
  if (problems.length > 0) return `Не адрес сайта — ${problems.join('; ')}`;
  const { sites } = countExamples(text);
  if (sites > MAX_EXAMPLES) {
    return `Примеров ${sites} — берём до ${MAX_EXAMPLES}: больше тему не уточняют, а ждать дольше. Оставьте ${MAX_EXAMPLES} самых похожих на то, что ищете`;
  }
  return null;
}

/** «3 сайта · 1 повтор не в счёт» — под полем, пока его заполняют. */
export function examplesCaption({ sites, repeats }: ExamplesCount): string {
  const counted = `${sites} ${plural(sites, 'сайт', 'сайта', 'сайтов')} из ${MAX_EXAMPLES}`;
  if (repeats === 0) return counted;
  return `${counted} · ${repeats} ${plural(repeats, 'повтор', 'повтора', 'повторов')} не в счёт`;
}
