/**
 * Чистые правила мастера загрузки: сопоставление колонок и сводка отчёта.
 *
 * **Поле у колонки одно, и колонка у поля одна.** Сервер принимает
 * сопоставление словарём «поле → колонка» (`columns.Mapping`), и две колонки
 * на одно поле в нём невозможны по форме. Правка руками держит то же правило:
 * назначить поле колонке — значит снять это поле с прежней колонки и снять
 * прежнее поле с этой. Иначе человек видел бы «почта» у двух колонок, а на
 * сервер ушла бы одна — та, что записана последней.
 *
 * **Отчёт — по строкам, итог — по причинам.** Сервер называет каждую строку
 * отдельно (так ищут строку в файле); итог после загрузки читают иначе:
 * «нет адреса — 2, строки 7 и 12». Частые причины первыми — с них и начинают
 * чинить файл. Замечания у загруженных строк — не отказы: считаются отдельно.
 */

import type { ImportProblem, LeadField } from '../api/salesTypes';

/** Поле → колонка с нуля, как в ответе и запросе сервера. */
export type FieldMapping = Partial<Record<LeadField, number>>;

function entriesOf(mapping: FieldMapping): [LeadField, number][] {
  return Object.entries(mapping) as [LeadField, number][];
}

/** Какое поле лежит в колонке; `null` — колонка не грузится. */
export function fieldAt(mapping: FieldMapping, column: number): LeadField | null {
  return entriesOf(mapping).find(([, index]) => index === column)?.[0] ?? null;
}

/** Назначить колонке поле (или снять, `null`): прежняя колонка этого поля
 *  и прежнее поле этой колонки освобождаются. */
export function withField(
  mapping: FieldMapping,
  column: number,
  field: LeadField | null,
): FieldMapping {
  const next: FieldMapping = {};
  for (const [known, index] of entriesOf(mapping)) {
    if (index !== column && known !== field) next[known] = index;
  }
  if (field !== null) next[field] = column;
  return next;
}

export interface ProblemGroup {
  reason: string;
  /** Строки файла по порядку. */
  lines: number[];
}

/** Строки отчёта одной судьбы (отклонённые или загруженные с замечанием),
 *  сгруппированные по причине: частые первыми, при равенстве — чья строка раньше. */
export function groupProblems(problems: ImportProblem[], loaded: boolean): ProblemGroup[] {
  const found = new Map<string, number[]>();
  for (const problem of problems) {
    if (problem.loaded !== loaded) continue;
    const lines = found.get(problem.reason);
    if (lines === undefined) found.set(problem.reason, [problem.line]);
    else lines.push(problem.line);
  }
  return [...found]
    .map(([reason, lines]) => ({ reason, lines: lines.sort((a, b) => a - b) }))
    .sort((a, b) => b.lines.length - a.lines.length || (a.lines[0] ?? 0) - (b.lines[0] ?? 0));
}

/** Отклонённые строки по причинам — то, что стоило лидов. */
export function groupRejections(problems: ImportProblem[]): ProblemGroup[] {
  return groupProblems(problems, false);
}

/** Номера строк словами: «строка 20», «строки 7, 12», длинный список —
 *  первые `shown` и «и ещё N»: искать в файле будут по первым. */
export function linesOf(lines: number[], shown = 12): string {
  const word = lines.length === 1 ? 'строка' : 'строки';
  const head = lines.slice(0, shown).join(', ');
  const rest = lines.length - shown;
  return rest > 0 ? `${word} ${head} и ещё ${rest}` : `${word} ${head}`;
}
