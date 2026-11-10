/**
 * Подробности записи журнала — итог одной строкой, остальное по запросу.
 *
 * Проверка прода 10.11.2026: у «Письмо отправлено» подробностей шесть-семь строк — кому, этап,
 * донор, от кого, транспорт, «ушло на самом деле: да», — и 34 строки из 50 на странице журнала
 * стояли высотой 142–177 px, страница — 7 016 px. Теперь на виду — до двух пар одной строкой
 * (у письма — кому и от кого), остальное — раскрытием строки. Одну строку не прячут: кнопка
 * «+1 ещё» заняла бы столько же места, сколько она сама.
 *
 * **Норма не показывается, отклонение — на виду.** Письмо ушло по-настоящему — транспорт у
 * настоящей отправки один, и «ушло на самом деле: да» не говорит ничего. Не ушло (нулевой
 * транспорт) — строка «ушло на самом деле: нет» стоит над итогом, транспорт — в раскрытии.
 *
 * **Длинное значение — только в раскрытии**: перечень прав или доменов чистки в итоге растил бы
 * строку так же, как прежняя простыня.
 */

import { detailLines } from '../api/auditLabels';
import type { DetailLine } from '../api/auditLabels';

/** Ушло ли письмо на самом деле — ключ записи «Письмо отправлено» (`letters/settle.py`). */
const REAL = 'ушло на самом деле';

/** В норме не показываются: у настоящей отправки транспорт один. */
const QUIET_WHEN_REAL = [REAL, 'транспорт'];

/** Читаются первыми: у письма — кому и от кого. Остальные — в порядке записи. */
const LEAD = ['кому', 'от кого'];

/** Пар в итоге. */
export const FACE_PAIRS = 2;

/** Значение длиннее — только в раскрытии: почта и домен короче, перечни — длиннее. */
export const FACE_CHARS = 80;

export interface DetailView {
  /** Отклонение от нормы — над итогом и всегда на виду. */
  alerts: DetailLine[];
  /** Итог: до двух пар одной строкой. */
  face: DetailLine[];
  /** Строка под итогом без раскрытия — одна короткая: прятать её незачем. */
  more: DetailLine[];
  /** По запросу — «+N ещё». */
  folded: DetailLine[];
}

const short = (line: DetailLine) => line.value.length <= FACE_CHARS;

/** Место ключа: пары письма — первыми, прочие — за ними в порядке записи (сортировка устойчива). */
function rank(line: DetailLine): number {
  const lead = LEAD.indexOf(line.label);
  return lead < 0 ? LEAD.length : lead;
}

/** Подробности записи: что на виду, а что — по запросу. `skip` — показанное в другом месте. */
export function detailView(
  details: Record<string, unknown> | null,
  skip: ReadonlySet<string> = new Set(),
): DetailView {
  const quiet = details?.[REAL] === true ? QUIET_WHEN_REAL : [];
  const lines = detailLines(details, new Set([...skip, ...quiet]));
  const rest = lines.filter((line) => line.label !== REAL).sort((a, b) => rank(a) - rank(b));
  const face = rest.filter(short).slice(0, FACE_PAIRS);
  const left = rest.filter((line) => !face.includes(line));
  const single = left.length === 1 && left.every(short);
  return {
    alerts: lines.filter((line) => line.label === REAL),
    face,
    more: single ? left : [],
    folded: single ? [] : left,
  };
}
